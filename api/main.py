from fastapi import FastAPI

app = FastAPI(title="ve-python-api")


@app.get("/")
def root() -> dict[str, str]:
    return {"status": "ok", "service": "python-api"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}
