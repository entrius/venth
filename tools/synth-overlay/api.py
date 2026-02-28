import sys
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from synth_client import SynthClient

app = FastAPI(title="Synth Overlay API Bridge")

# Enable CORS for the Chrome Extension
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = SynthClient()

@app.get("/api/edge")
def get_polymarket_edge(market_type: str = "daily"):
    """
    Returns edge calculations by comparing Synth predictions 
    with Polymarket probabilities.
    """
    try:
        if market_type == "daily":
            data = client.get_polymarket_daily()
        elif market_type == "hourly":
            data = client.get_polymarket_hourly()
        else:
            return {"error": "Unsupported market type"}
        
        synth_up = data.get("synth_probability_up", 0) * 100
        poly_up = data.get("polymarket_probability_up", 0) * 100
        
        # Calculate edge
        edge_up = synth_up - poly_up
        edge_down = (100 - synth_up) - (100 - poly_up)

        return {
            "market_slug": data.get("slug"),
            "synth_up": synth_up,
            "poly_up": poly_up,
            "edge_up": edge_up,
            "edge_down": edge_down,
            "confidence": "High" if abs(edge_up) > 2 else "Low",
            "decision": "NO TRADE" if abs(edge_up) <= 2 else ("BUY YES" if edge_up > 0 else "BUY NO")
        }
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
