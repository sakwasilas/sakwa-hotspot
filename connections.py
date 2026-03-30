from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session, declarative_base

DATABASE_URL = "mysql+pymysql://root:2480@localhost/hotspot_business"


engine = create_engine(DATABASE_URL, echo=True)


Session = scoped_session(sessionmaker(bind=engine))


SessionLocal = Session


Base = declarative_base()