/**
 * WebSocket client with auto-reconnect.
 */
function createWSClient(port) {
    const state = Alpine.reactive({
        connected: false,
        events: [],
        maxEvents: 200,
    });

    let ws = null;
    let reconnectTimer = null;
    let reconnectDelay = 1000;

    function connect() {
        const url = `ws://${location.hostname}:${port}/ws`;
        ws = new WebSocket(url);

        ws.onopen = () => {
            state.connected = true;
            reconnectDelay = 1000;
            console.log('[WS] Connected');
        };

        ws.onclose = () => {
            state.connected = false;
            console.log('[WS] Disconnected, reconnecting in', reconnectDelay, 'ms');
            reconnectTimer = setTimeout(() => {
                reconnectDelay = Math.min(reconnectDelay * 1.5, 10000);
                connect();
            }, reconnectDelay);
        };

        ws.onerror = (e) => {
            console.warn('[WS] Error:', e);
        };

        ws.onmessage = (evt) => {
            try {
                const data = JSON.parse(evt.data);
                if (data.type === 'history') {
                    // Initial catch-up
                    for (const ev of data.events) {
                        pushEvent(ev);
                    }
                } else if (data.type === 'pong') {
                    // Ignore
                } else {
                    pushEvent(data);
                    // Dispatch custom event for pages to listen to
                    window.dispatchEvent(new CustomEvent('ws-event', { detail: data }));
                }
            } catch (e) {
                console.warn('[WS] Parse error:', e);
            }
        };
    }

    function pushEvent(ev) {
        state.events.unshift(ev);
        if (state.events.length > state.maxEvents) {
            state.events.length = state.maxEvents;
        }
    }

    function close() {
        if (reconnectTimer) clearTimeout(reconnectTimer);
        if (ws) ws.close();
    }

    connect();

    return { state, close };
}
