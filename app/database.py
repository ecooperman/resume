import os

from shared_python import owned
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# DATABASE_URL lets migrations (see migrations/env.py), tests or a scratch copy of the data
# use another database; the default is the app's own file.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./access.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Every person's data is filtered to them below the routes (see shared_python.owned): hooks every session in the process.
owned.install()
