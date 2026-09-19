# roda web_hub.pyc numa porta fixa (8790) sem colidir com a instancia real
import importlib.util, os, sys, http.server
sys.path.insert(0, r"D:\fita led"); os.chdir(r"D:\fita led"); os.environ["NEXUS_NOBROWSER"] = "1"
spec = importlib.util.spec_from_file_location("web_hub", r"D:\fita led\web_hub.pyc")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8790), m.H)
print("[test] http://127.0.0.1:8790", flush=True); srv.serve_forever()
