# gunicorn이 실제 요청을 처리하는 worker 프로세스를 만든 "직후"에 실행됨.
# 이렇게 해야 백그라운드 갱신 스레드가 실제로 요청에 응답하는 그 프로세스 안에서 돌아감.


def post_fork(server, worker):
    from app import start_background_threads
    start_background_threads()
    server.log.info(f"[gunicorn] worker {worker.pid}에서 갱신 스레드 시작")
