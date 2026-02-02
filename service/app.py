"""Attestable builds API service."""

from fastapi import FastAPI

from service.routes import router

app = FastAPI(title="Attestable Builds Service")
app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
