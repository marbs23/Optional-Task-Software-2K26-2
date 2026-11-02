import os
import sqlite3
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import customers, orders, products
from app.errors import DomainError
from app.database import Database


def create_app(database_path=None):
    database = Database(database_path if database_path is not None else os.getenv("LAB_DB_PATH", "data/store.sqlite3"))

    @asynccontextmanager
    async def lifespan(application):
        database.initialize()
        yield

    application = FastAPI(title="Tienda Lab", version="1.0.0", lifespan=lifespan)
    application.state.database = database
    application.include_router(products.router)
    application.include_router(customers.router)
    application.include_router(orders.router)

    @application.exception_handler(sqlite3.OperationalError)
    async def database_error(request: Request, error: sqlite3.OperationalError):
        return JSONResponse(status_code=503, content={"detail": "Base de datos no disponible; reintente más tarde"})

    @application.exception_handler(DomainError)
    async def domain_error(request: Request, error: DomainError):
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

    return application


app = create_app()


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
