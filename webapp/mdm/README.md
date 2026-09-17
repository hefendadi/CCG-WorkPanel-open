# Public MDM

MDM uses SQLAlchemy with MySQL, environment-supplied MDM_DATABASE_URL and one
public Alembic baseline. No business source files are required.

Use the local Docker quick start and from-scratch generators in the root README.
Run alembic -c webapp/mdm/alembic.ini upgrade head only against the isolated demo.
