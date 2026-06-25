"""Quick smoke-test: import the app and print all registered routes."""
from app.main import app

print("App loaded OK. Routes:")
for route in app.routes:
    if hasattr(route, "methods"):
        methods = ",".join(route.methods)
        print(f"  {route.path} [{methods}]")
