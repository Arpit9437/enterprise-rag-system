from fastapi import FastAPI, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.api.routes.auth import router as auth_router
from app.api.routes.tenants import router as tenants_router

app = FastAPI()

app.include_router(auth_router)
app.include_router(tenants_router)

@app.get("/health")
def health_check():
    return {"mssg" : "FastAPI application running."}

@app.get("/db-check")
def check_db(session: Session = Depends(get_db)):
    session.execute(text("SELECT 1"))
    return {"success": True, "message": "Database connection successful"}
