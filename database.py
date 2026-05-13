from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

DATABASE_URL = os.getenv("DATABASE_URL=postgres://DZqWAvUhwrHBWabrlJgNBrYbDVmufWnu@yamabiko.proxy.rlwy.net:36404/railway", "sqlite:///./test.db")

engine = create_engine(DATABASE_URL=postgres://DZqWAvUhwrHBWabrlJgNBrYbDVmufWnu@yamabiko.proxy.rlwy.net:36404/railway)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
