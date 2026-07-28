from server.config import Settings

if __name__ == "__main__":
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(
        "server.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
