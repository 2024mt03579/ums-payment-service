from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

Base = declarative_base()
engine = None
SessionLocal = None

def init_db(database_url: str):
    global engine, SessionLocal
    if engine is None:
        engine = create_engine(database_url)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        # create tables
        from app import models
        Base.metadata.create_all(bind=engine)