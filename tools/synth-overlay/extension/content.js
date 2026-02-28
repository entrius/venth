// Synth Overlay Content Script
console.log("Synth Overlay: Initializing Polymarket Extension");

// Basic DOM injection function
function injectBadge(edgeUp, decision, confidence) {
    // Only inject if there's a recognized Polymarket decision header or box
    // For this demonstration/mock, we target the body and affix a floating badge.
    const container = document.createElement("div");
    container.id = "synth-overlay-badge";
    container.style = `
        position: fixed;
        bottom: 20px;
        right: 20px;
        background: #111;
        border: 2px solid ${edgeUp > 0 ? '#4caf50' : '#f44336'};
        color: white;
        padding: 15px;
        border-radius: 8px;
        z-index: 999999;
        font-family: inherit;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
    `;

    container.innerHTML = `
        <h3 style="margin: 0 0 5px 0; font-size: 14px;">Synth Data Edge</h3>
        <p style="margin: 0; font-size: 12px; font-weight: bold; color: ${edgeUp > 0 ? '#4caf50' : '#f44336'};">YES Edge: ${edgeUp > 0 ? '+' : ''}${edgeUp.toFixed(1)}%</p>
        <p style="margin: 5px 0 0 0; font-size: 11px;">Signal: <strong>${decision}</strong> (Confidence: ${confidence})</p>
    `;

    document.body.appendChild(container);
}

// Fetch bridge API (assuming synth API proxy runs locally on port 8000)
async function fetchEdgeData() {
    try {
        const res = await fetch("http://127.0.0.1:8000/api/edge?market_type=daily");
        if (res.ok) {
            const data = await res.json();
            if (data && data.edge_up !== undefined) {
                injectBadge(data.edge_up, data.decision, data.confidence);
            }
        } else {
            console.error("Synth Overlay: API Bridge failed to respond.");
        }
    } catch (err) {
        console.error("Synth Overlay: Cannot reach local API bridge at port 8000.", err);
    }
}

// Check on load
setTimeout(fetchEdgeData, 1500);
