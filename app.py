# -*- coding: utf-8 -*-
"""
SK하이닉스 본주-ADR 괴리율 로컬 대시보드
실행: python app.py
브라우저: http://localhost:5000

필요 패키지:
    pip install flask yfinance --break-system-packages
"""

import time
import threading
import socket
import pandas as pd
from flask import Flask, jsonify, render_template_string
import yfinance as yf

# 네트워크 요청이 응답 없이 무한정 걸려있는 것을 방지 (초 단위)
socket.setdefaulttimeout(15)

app = Flask(__name__)

# ---- 설정 ----
ADR_TICKER = "SKHY"
KRX_TICKER = "000660.KS"
FX_TICKER = "KRW=X"
ADR_RATIO = 10          # ADR 1주 = 본주 1/10주
FETCH_INTERVAL_SEC = 30  # 서버가 실제로 새 데이터를 가져오는 주기 (너무 짧으면 야후 쪽에서 막을 수 있음)

# ---- 캐시 (서버 메모리에 최신 데이터 저장) ----
cache = {
    "adr_price": None,
    "krx_price": None,
    "fx_rate": None,
    "theo_price": None,
    "premium_pct": None,
    "updated_at": None,
    "error": None,
}
cache_lock = threading.Lock()

# ---- 히스토리 캐시 (일별 시계열) ----
history_cache = {"data": [], "updated_at": None, "error": None}
history_lock = threading.Lock()
HISTORY_REFRESH_SEC = 300  # 5분마다 히스토리 갱신 (일별 데이터라 자주 안 바뀜)


def fetch_history(period="3mo", interval="1d"):
    """상장일부터 지금까지 일별 시세를 모아 날짜별 괴리율 계산"""
    adr_close = yf.Ticker(ADR_TICKER).history(period=period, interval=interval)["Close"]
    krx_close = yf.Ticker(KRX_TICKER).history(period=period, interval=interval)["Close"]
    fx_close = yf.Ticker(FX_TICKER).history(period=period, interval=interval)["Close"]

    # 타임존 제거 + 날짜만 남기기 (한국/미국 장 시간대가 달라서 날짜 기준으로 맞춤)
    for s in (adr_close, krx_close, fx_close):
        s.index = s.index.tz_localize(None).normalize()

    df = pd.DataFrame({"adr": adr_close, "krx": krx_close, "fx": fx_close}).sort_index()
    df["krx"] = df["krx"].ffill()
    df["fx"] = df["fx"].ffill()
    df = df.dropna(subset=["adr"])  # SKHY가 실제로 거래된 날짜만 남김 (상장 전 날짜 제거)

    df["theo"] = (df["krx"] / ADR_RATIO) / df["fx"]
    df["premium"] = (df["adr"] - df["theo"]) / df["theo"] * 100
    return df


def fetch_and_update_history():
    try:
        df = fetch_history()
        records = [
            {
                "date": idx.strftime("%Y-%m-%d"),
                "adr": round(row["adr"], 2),
                "krx": round(row["krx"], 0),
                "fx": round(row["fx"], 2),
                "theo": round(row["theo"], 2),
                "premium": round(row["premium"], 2),
            }
            for idx, row in df.iterrows()
        ]
        with history_lock:
            history_cache["data"] = records
            history_cache["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            history_cache["error"] = None
        print(f"[history] {len(records)}개 날짜 갱신 완료")
    except Exception as e:
        with history_lock:
            history_cache["error"] = str(e)
        print(f"[history fetch error] {e}")


def history_loop():
    while True:
        fetch_and_update_history()
        time.sleep(HISTORY_REFRESH_SEC)


def fetch_and_update():
    """yfinance로 3개 티커를 가져와 괴리율을 계산하고 캐시에 저장"""
    try:
        print(f"[fetch] 시도 시작 ({time.strftime('%H:%M:%S')})")

        print("[fetch] SKHY 요청 중...")
        adr = yf.Ticker(ADR_TICKER).fast_info["last_price"]
        print(f"[fetch] SKHY 완료: {adr}")

        print("[fetch] 000660.KS 요청 중...")
        krx = yf.Ticker(KRX_TICKER).fast_info["last_price"]
        print(f"[fetch] 000660.KS 완료: {krx}")

        print("[fetch] KRW=X 요청 중...")
        fx = yf.Ticker(FX_TICKER).fast_info["last_price"]
        print(f"[fetch] KRW=X 완료: {fx}")

        theo = (krx / ADR_RATIO) / fx
        premium = (adr - theo) / theo * 100

        with cache_lock:
            cache["adr_price"] = adr
            cache["krx_price"] = krx
            cache["fx_rate"] = fx
            cache["theo_price"] = theo
            cache["premium_pct"] = premium
            cache["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            cache["error"] = None

        print(f"[{cache['updated_at']}] SKHY=${adr:.2f} 000660=₩{krx:,.0f} "
              f"USDKRW={fx:.2f} 이론가=${theo:.2f} 괴리율={premium:+.2f}%")

    except Exception as e:
        with cache_lock:
            cache["error"] = str(e)
        print(f"[fetch error] {type(e).__name__}: {e}")


def background_loop():
    """백그라운드에서 계속 갱신"""
    while True:
        fetch_and_update()
        time.sleep(FETCH_INTERVAL_SEC)


@app.route("/api/quote")
def api_quote():
    with cache_lock:
        return jsonify(dict(cache))


@app.route("/api/history")
def api_history():
    with history_lock:
        return jsonify(dict(history_cache))


@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


INDEX_HTML = """
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SK하이닉스 ADR 괴리율</title>
<style>
  :root{
    --bg:#0b0f14; --panel:#121821; --panel2:#0e141c; --border:#232c38;
    --text:#e8edf3; --sub:#8b98a9; --up:#ff5c5c; --down:#4d8dff; --accent:#f2b632;
    --mono:'SF Mono','Roboto Mono',Consolas,monospace;
  }
  *{box-sizing:border-box;}
  body{margin:0;background:var(--bg);color:var(--text);
    font-family:-apple-system,BlinkMacSystemFont,'Pretendard','Malgun Gothic',sans-serif;
    padding:32px 20px 60px;}
  .wrap{max-width:920px;margin:0 auto;}
  .eyebrow{font-family:var(--mono);font-size:12px;letter-spacing:.12em;color:var(--accent);
    text-transform:uppercase;margin-bottom:8px;}
  h1{font-size:26px;margin:0 0 6px;font-weight:700;letter-spacing:-.01em;}
  .desc{color:var(--sub);font-size:14px;line-height:1.6;}
  .status{display:flex;align-items:center;gap:8px;font-family:var(--mono);font-size:12px;
    color:var(--sub);margin:18px 0 22px;}
  .dot{width:7px;height:7px;border-radius:50%;background:#3ddc84;}
  .dot.err{background:#ff5c5c;}
  .hero{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--border);
    border-radius:14px;padding:28px;text-align:center;margin-bottom:20px;}
  .hero .label{font-family:var(--mono);font-size:12px;color:var(--sub);letter-spacing:.08em;
    text-transform:uppercase;}
  .hero .value{font-size:56px;font-weight:800;letter-spacing:-.02em;margin:8px 0;
    font-variant-numeric:tabular-nums;}
  .hero .sub{font-family:var(--mono);font-size:13px;color:var(--sub);}
  .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:20px;}
  .card{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:18px;}
  .card .k{font-family:var(--mono);font-size:11px;color:var(--sub);letter-spacing:.06em;
    text-transform:uppercase;margin-bottom:8px;}
  .card .v{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums;}
  .calc{background:var(--panel2);border:1px solid var(--border);border-radius:12px;
    padding:20px 22px;margin-bottom:20px;}
  .calc h3{font-size:13px;color:var(--sub);margin:0 0 14px;font-weight:600;
    letter-spacing:.04em;text-transform:uppercase;}
  .calc .row{display:flex;justify-content:space-between;align-items:baseline;
    font-family:var(--mono);font-size:13px;padding:7px 0;border-bottom:1px dashed var(--border);}
  .calc .row:last-child{border-bottom:none;}
  .calc .row .l{color:var(--sub);}
  .calc .row .r{font-weight:600;}
  footer{font-size:12px;color:var(--sub);line-height:1.7;margin-top:28px;}
  .errbox{background:#2a1414;border:1px solid #5c2626;border-radius:10px;padding:16px;
    font-size:13px;color:#ffb4b4;margin-bottom:20px;line-height:1.6;}
  @media(max-width:640px){.grid{grid-template-columns:1fr;}.hero .value{font-size:40px;}}
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
</head>
<body>
<div class="wrap">
  <div class="eyebrow">Live ADR Arbitrage Monitor · Local Server</div>
  <h1>SK하이닉스 본주 · ADR 괴리율</h1>
  <div class="desc">
    서버가 백그라운드에서 30초마다 yfinance로 시세를 갱신하고, 이 페이지는 그 결과를 자동으로 불러옵니다.
  </div>

  <div class="status">
    <span class="dot" id="statusDot"></span>
    <span id="statusText">불러오는 중…</span>
  </div>

  <div id="errArea"></div>

  <div class="hero">
    <div class="label">ADR 괴리율 (Premium / Discount)</div>
    <div class="value" id="premiumValue">—</div>
    <div class="sub" id="premiumSub">SKHY 실제가 vs 이론 환산가</div>
  </div>

  <div class="grid">
    <div class="card"><div class="k">SKHY (Nasdaq)</div><div class="v" id="adrPrice">—</div></div>
    <div class="card"><div class="k">000660 (KRX)</div><div class="v" id="krxPrice">—</div></div>
    <div class="card"><div class="k">USD/KRW</div><div class="v" id="fxRate">—</div></div>
  </div>

  <div class="calc">
    <h3>계산 상세</h3>
    <div class="row"><span class="l">이론가치(USD) = (000660 ÷ 10) ÷ USDKRW</span><span class="r" id="calcTheo">—</span></div>
    <div class="row"><span class="l">괴리율(%) = (SKHY − 이론가치) ÷ 이론가치 × 100</span><span class="r" id="calcPremium">—</span></div>
  </div>

  <div class="calc">
    <h3>괴리율 추이 (상장일부터)</h3>
    <div style="position:relative;height:280px;">
      <canvas id="historyChart"></canvas>
    </div>
    <div style="font-family:var(--mono);font-size:11px;color:var(--sub);margin-top:10px;" id="historyStatus">불러오는 중…</div>
  </div>

  <footer>
    데이터 출처: yfinance (Yahoo Finance) · 서버가 30초마다 갱신, 페이지는 5초마다 서버에 확인합니다.<br>
    이 창을 열어두는 동안 터미널에서 app.py가 계속 실행 중이어야 합니다.
  </footer>
</div>

<script>
function fmt(n, d=2){
  if(n===null||n===undefined||isNaN(n)) return "—";
  return n.toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
}

async function poll(){
  try{
    const res = await fetch('/api/quote');
    const d = await res.json();
    const dot = document.getElementById('statusDot');
    const statusText = document.getElementById('statusText');
    const errArea = document.getElementById('errArea');

    if(d.error){
      dot.className = 'dot err';
      statusText.textContent = '서버 갱신 오류';
      errArea.innerHTML = '<div class="errbox">⚠️ ' + d.error + '</div>';
      return;
    }
    if(d.adr_price === null){
      statusText.textContent = '첫 데이터 가져오는 중…';
      return;
    }

    errArea.innerHTML = '';
    dot.className = 'dot';
    statusText.textContent = '마지막 서버 갱신: ' + d.updated_at;

    document.getElementById('adrPrice').textContent = '$' + fmt(d.adr_price);
    document.getElementById('krxPrice').textContent = '₩' + fmt(d.krx_price, 0);
    document.getElementById('fxRate').textContent = fmt(d.fx_rate);

    const sign = d.premium_pct >= 0 ? '+' : '';
    const premiumEl = document.getElementById('premiumValue');
    premiumEl.textContent = sign + fmt(d.premium_pct) + '%';
    premiumEl.style.color = d.premium_pct >= 0 ? 'var(--up)' : 'var(--down)';
    document.getElementById('premiumSub').textContent =
      d.premium_pct >= 0 ? 'ADR이 이론가 대비 프리미엄' : 'ADR이 이론가 대비 할인';

    document.getElementById('calcTheo').textContent = '$' + fmt(d.theo_price);
    document.getElementById('calcPremium').textContent = sign + fmt(d.premium_pct) + '%';

  }catch(e){
    document.getElementById('statusDot').className = 'dot err';
    document.getElementById('statusText').textContent = '서버 연결 실패 (app.py 실행 중인지 확인)';
  }
}

poll();
setInterval(poll, 5000); // 페이지는 5초마다 서버에 확인

// ---- 히스토리 차트 ----
let historyChart = null;

async function loadHistory(){
  const statusEl = document.getElementById('historyStatus');
  try{
    const res = await fetch('/api/history');
    const d = await res.json();

    if(d.error){
      statusEl.textContent = '히스토리 오류: ' + d.error;
      return;
    }
    if(!d.data || d.data.length === 0){
      statusEl.textContent = '아직 히스토리 데이터가 없습니다 (곧 채워집니다)';
      return;
    }

    const labels = d.data.map(r => r.date);
    const premiums = d.data.map(r => r.premium);

    const ctx = document.getElementById('historyChart').getContext('2d');
    if(historyChart){
      historyChart.data.labels = labels;
      historyChart.data.datasets[0].data = premiums;
      historyChart.update();
    }else{
      historyChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels: labels,
          datasets: [{
            label: '괴리율 (%)',
            data: premiums,
            borderColor: '#f2b632',
            backgroundColor: 'rgba(242,182,50,0.1)',
            borderWidth: 2,
            pointRadius: 3,
            pointBackgroundColor: '#f2b632',
            fill: true,
            tension: 0.2
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: (ctx) => '괴리율: ' + ctx.parsed.y.toFixed(2) + '%'
              }
            }
          },
          scales: {
            x: { ticks: { color: '#8b98a9', font: { family: 'monospace', size: 10 } }, grid: { color: '#232c38' } },
            y: { ticks: { color: '#8b98a9', font: { family: 'monospace', size: 10 }, callback: (v) => v + '%' }, grid: { color: '#232c38' } }
          }
        }
      });
    }

    statusEl.textContent = '히스토리 마지막 갱신: ' + d.updated_at + ' · ' + d.data.length + '개 거래일';

  }catch(e){
    statusEl.textContent = '히스토리 불러오기 실패';
  }
}

loadHistory();
setInterval(loadHistory, 60000); // 히스토리는 1분마다 재확인 (서버는 5분마다만 실제 갱신)
</script>
</body>
</html>
"""


def start_background_threads():
    """실시간 시세 + 히스토리 갱신 스레드 시작.
    gunicorn(배포)이든 python app.py(로컬)든 모듈이 로드될 때 항상 실행됨."""
    t = threading.Thread(target=background_loop, daemon=True)
    t.start()
    t2 = threading.Thread(target=history_loop, daemon=True)
    t2.start()


start_background_threads()

if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 5000))

    print("=" * 50)
    print("SK하이닉스 ADR 괴리율 대시보드 서버 시작")
    print(f"브라우저에서 http://localhost:{port} 열어주세요")
    print("=" * 50)

    app.run(host="0.0.0.0", port=port, debug=False)
