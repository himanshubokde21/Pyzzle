import os, time
from pyzzle import Base, Column, Integer, String, connect, disconnect

URL = os.environ["PYZZLE_TEST_DATABASE_URL"]

class User(Base):
    __tablename__ = "pyzzle_test_demo"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)

def timed(label, fn):
    t = time.perf_counter()
    result = fn()
    print(f"{label}: {(time.perf_counter() - t) * 1000:.1f} ms")
    return result

timed("connect", lambda: connect(URL))
Base.execute('DROP TABLE IF EXISTS "pyzzle_test_demo"')
timed("create_tables", Base.create_tables)
user = timed("create", lambda: User.create(name="Amit"))
user = timed("get", lambda: User.get(user.id))
user.name = "Rahul"
timed("save", user.save)
timed("delete", user.delete)
timed("100 x SELECT 1", lambda: [Base.execute("SELECT 1") for _ in range(100)])
Base.execute('DROP TABLE IF EXISTS "pyzzle_test_demo"')
timed("disconnect", disconnect)
