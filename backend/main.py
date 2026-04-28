from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, engine
from .routers import ai, alerts, analysis, etf, portfolio

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Vaultis Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501",
        "http://127.0.0.1:8501",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "*",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(etf.router)
app.include_router(portfolio.router)
app.include_router(analysis.router)
app.include_router(alerts.router)
app.include_router(ai.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
