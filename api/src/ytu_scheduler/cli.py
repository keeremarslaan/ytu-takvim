import uvicorn


def main() -> None:
    uvicorn.run("ytu_scheduler.main:app", host="127.0.0.1", port=8000, reload=True)
