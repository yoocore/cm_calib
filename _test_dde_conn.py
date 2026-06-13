"""Simple DDE connectivity test - run with CarMaker running."""
import win32ui
import dde
import sys

server = dde.CreateServer()
server.Create("DdeTestConn")
conv = dde.CreateConversation(server)
try:
    conv.ConnectTo("TclEval", "CarMaker")
    conv.Exec("puts {DDE from Python OK}")
    print("DDE: CarMaker TclEval connected OK")
    
    conv2 = dde.CreateConversation(server)
    try:
        conv2.ConnectTo("TclEval", "IPG-MOVIE")
        conv2.Exec("puts {DDE to IPG-MOVIE OK}")
        print("DDE: IPG-MOVIE connected OK")
    except Exception as e2:
        print(f"DDE: IPG-MOVIE failed (expected if no TestRun loaded): {e2}")
except Exception as e:
    print(f"DDE ERROR: {e}")
    sys.exit(1)
finally:
    server.Shutdown()
