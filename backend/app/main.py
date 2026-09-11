import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import ingestion, economist, auth_router

app = FastAPI(
    title="Private Capital Flow AI System API",
    description="Bank of Tanzania Automated PCF Statutory Verification & Audit Engine",
    version="1.0.0"
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins for network testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure local storage directory exists
os.makedirs("./storage/raw_documents", exist_ok=True)

# Register API Routers
app.include_router(auth_router.router)
app.include_router(ingestion.router)
app.include_router(economist.router)

@app.get("/")
async def root():
    return {
        "system": "Private Capital Flow AI Platform",
        "status": "ONLINE",
        "bot_audit_engine": "ACTIVE"
    }