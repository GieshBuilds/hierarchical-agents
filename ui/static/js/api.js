/**
 * API client for the hierarchy management UI.
 */
const API = {
    base: '',

    async get(path) {
        const res = await fetch(`${this.base}${path}`);
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
        return res.json();
    },

    async post(path, body) {
        const res = await fetch(`${this.base}${path}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
        return res.json();
    },

    async put(path, body) {
        const res = await fetch(`${this.base}${path}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
        return res.json();
    },

    async patch(path, body) {
        const res = await fetch(`${this.base}${path}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
        return res.json();
    },

    async del(path) {
        const res = await fetch(`${this.base}${path}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
        return res.json();
    },

    // Shortcuts
    dashboard: ()          => API.get('/api/dashboard'),
    profiles:  ()          => API.get('/api/profiles'),
    profile:   (n)         => API.get(`/api/profiles/${n}`),
    orgTree:   (root)      => API.get(`/api/org-tree${root ? '?root=' + root : ''}`),
    gateways:  ()          => API.get('/api/gateways'),
    messages:  (params)    => API.get('/api/messages?' + new URLSearchParams(params)),
    msgStats:  ()          => API.get('/api/messages/stats'),
    workers:   (params)    => API.get('/api/workers?' + new URLSearchParams(params || {})),
    workerStats: ()        => API.get('/api/workers/stats'),
    chains:    (params)    => API.get('/api/chains?' + new URLSearchParams(params || {})),
    chain:     (id)        => API.get(`/api/chains/${id}`),
    memoryProfiles: ()     => API.get('/api/memory'),
    memoryEntries: (p, params) => API.get(`/api/memory/${p}?` + new URLSearchParams(params || {})),
    soulMd:    (p)         => API.get(`/api/files/${p}/soul`),
    profileFiles: (p)      => API.get(`/api/files/${p}/files`),
    readFile:  (p, f)      => API.get(`/api/files/${p}/file/${f}`),
    correlation: (id)      => API.get(`/api/messages/correlation/${id}`),

    saveSoul:      (p, content) => API.put(`/api/files/${p}/soul`, { content }),
    saveFile:      (p, f, content) => API.put(`/api/files/${p}/file/${f}`, { content }),
    createProfile: (data)  => API.post('/api/profiles', data),
    updateProfile: (n, data) => API.patch(`/api/profiles/${n}`, data),
    deleteProfile: (n)     => API.del(`/api/profiles/${n}`),
    activateProfile: (n)   => API.post(`/api/profiles/${n}/activate`),
    suspendProfile:  (n)   => API.post(`/api/profiles/${n}/suspend`),
    startGateway:  (n)     => API.post(`/api/gateways/${n}/start`),
    stopGateway:   (n)     => API.post(`/api/gateways/${n}/stop`),
    sendMessage:   (data)  => API.post('/api/messages/send', data),
};
