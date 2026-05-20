from fastapi import FastAPI

from app.api import ingest, items

app = FastAPI(
    title="memory-dump",
    description="Personal memory dump: ingest links, analyze, embed, ask.",
    version="0.1.0",
)


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(ingest.router)
app.include_router(items.router)
