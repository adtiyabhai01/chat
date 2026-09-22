// Configuration
const API_BASE = '';
const USE_DUMMY_DATA = false; // Real API connected

// State
let currentPage = 'dashboard';
let currentUser = null;
let confirmCallback = null;
let usersData = [];
let filteredUsers = [];
let currentUserPage = 1;
const usersPerPage = 10;
let activityChart = null;

// Dummy Data (for testing without backend)
const dummyData = {
    stats: {
        totalUsers: 1247,
        activeUsers: 342,
        totalMessages: 45678,
        newSignups: 23
    },
    users: Array.from({ length: 50 }, (_, i) => ({
        id: i + 1,
        username: `user${i + 1}`,
        email: `user${i + 1}@example.com`,
        status: Math.random() > 0.2 ? 'active' : 'banned',
        lastActive: new Date(Date.now() - Math.random() * 7 * 24 * 60 * 60 * 1000).toISOString()
    })),
    conversations: [
        { id: 1, username: 'john_doe', lastMessage: 'Hey there!', unread: 2 },
        { id: 2, username: 'jane_smith', lastMessage: 'Thanks for the help', unread: 0 },
        { id: 3, username: 'bob_wilson', lastMessage: 'See you tomorrow', unread: 1 },
        { id: 4, username: 'alice_brown', lastMessage: 'Got it!', unread: 0 },
        { id: 5, username: 'charlie_davis', lastMessage: 'Perfect timing', unread: 3 }
    ],
    messages: {
        1: [
            { id: 1, sender: 'john_doe', text: 'Hey there!', timestamp: '2024-01-15 10:30', isSent: false },
            { id: 2, sender: 'admin', text: 'Hello! How can I help?', timestamp: '2024-01-15 10:31', isSent: true },
            { id: 3, sender: 'john_doe', text: 'Just checking in', timestamp: '2024-01-15 10:32', isSent: false }
        ],
        2: [
            { id: 4, sender: 'jane_smith', text: 'I need help with spam', timestamp: '2024-01-15 09:00', isSent: false, flagged: true },
            { id: 5, sender: 'admin', text: 'What seems to be the issue?', timestamp: '2024-01-15 09:05', isSent: true },
            { id: 6, sender: 'jane_smith', text: 'Thanks for the help', timestamp: '2024-01-15 09:10', isSent: false }
        ]
    },
    reports: [
        {
            id: 1,
            reporter: 'user123',
            reportedUser: 'spammer99',
            reason: 'Sending spam messages repeatedly',
            timestamp: '2024-01-15 14:30'
        },
        {
            id: 2,
            reporter: 'user456',
            reportedUser: 'baduser22',
            reason: 'Inappropriate content and harassment',
            timestamp: '2024-01-15 13:15'
        },
        {
            id: 3,
            reporter: 'user789',
            reportedUser: 'troll88',
            reason: 'Offensive language and bullying',
            timestamp: '2024-01-15 12:00'
        }
    ],
    chartData: [120, 150, 180, 220, 190, 250, 280]
};

// API Functions
async function fetchAPI(endpoint, options = {}) {
    try {
        const response = await fetch(`${API_BASE}${endpoint}`, {
            ...options,
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken'),
                ...options.headers
            }
        });
        if (!response.ok) throw new Error('API request failed');
        return await response.json();
    } catch (error) {
        console.error('API Error:', error);
        showToast('API request failed', 'error');
        throw error;
    }
}

function getCookie(name) {
    const v = document.cookie.match('(^|;) ?' + name + '=([^;]*)(;|$)');
    return v ? v[2] : '';
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initEventListeners();
    loadDashboard();
});

// Navigation
function initNavigation() {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const page = item.dataset.page;
            navigateTo(page);
        });
    });
}

function navigateTo(page) {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.toggle('active', item.dataset.page === page);
    });
    
    document.querySelectorAll('.page').forEach(p => {
        p.classList.toggle('active', p.id === page);
    });
    
    currentPage = page;
    
    // Load page data
    switch (page) {
        case 'dashboard': loadDashboard(); break;
        case 'users': loadUsers(); break;
        case 'chats': loadChats(); break;
        case 'reports': loadReports(); break;
        case 'system': loadSystem(); break;
    }
    
    // Close sidebar on mobile
    if (window.innerWidth <= 768) {
        document.getElementById('sidebar').classList.remove('active');
    }
}

// Event Listeners
function initEventListeners() {
    // Menu toggle
    document.getElementById('menuToggle').addEventListener('click', () => {
        document.getElementById('sidebar').classList.toggle('active');
    });
    
    // Logout
    document.getElementById('logoutBtn').addEventListener('click', () => {
        showConfirm('Logout', 'Are you sure you want to logout?', () => {
            showToast('Logged out successfully', 'success');
            setTimeout(() => window.location.href = '/login', 1000);
        });
    });
    
    // Modal
    document.getElementById('modalCancel').addEventListener('click', closeModal);
    document.getElementById('modalConfirm').addEventListener('click', () => {
        if (confirmCallback) confirmCallback();
        closeModal();
    });
    
    // User search and filter with debounce
    var userSearchTimeout = null;
    document.getElementById('searchUsers').addEventListener('input', function() {
        clearTimeout(userSearchTimeout);
        userSearchTimeout = setTimeout(filterUsers, 300);
    });
    document.getElementById('filterStatus').addEventListener('change', filterUsers);
    
    // System controls
    document.getElementById('maintenanceToggle').addEventListener('change', toggleMaintenance);
    document.getElementById('sendBroadcast').addEventListener('click', sendBroadcast);
    
    // Feature toggles
    ['registrationToggle', 'groupChatToggle', 'fileUploadToggle'].forEach(id => {
        document.getElementById(id).addEventListener('change', (e) => {
            const feature = id.replace('Toggle', '');
            showToast(`${feature} ${e.target.checked ? 'enabled' : 'disabled'}`, 'success');
        });
    });
}

// Dashboard
async function loadDashboard() {
    try {
        const stats = await fetchAPI('/admin-stats/');
        document.getElementById('totalUsers').textContent = stats.total_users;
        document.getElementById('activeUsers').textContent = stats.online_users || stats.active_users;
        document.getElementById('totalMessages').textContent = stats.total_messages;
        document.getElementById('newSignups').textContent = stats.recent_activity || 0;
        
        const chartDataRes = await fetchAPI('/admin-chart-data/');
        if (!chartDataRes.error && chartDataRes.messages) {
            renderChart(chartDataRes.messages);
        }
    } catch (error) {
        console.error('Failed to load dashboard:', error);
    }
}

// Auto-refresh dashboard every 30s
setInterval(function() {
    if (currentPage === 'dashboard') loadDashboard();
}, 30000);

function renderChart(chartData) {
    const canvas = document.getElementById('messagesChart');
    if (!canvas || typeof Chart === 'undefined') return;
    if (!chartData || !chartData.length) return;
    window.lastChartData = chartData;
    const ctx = canvas.getContext('2d');
    if (window.activityChart) window.activityChart.destroy();
    const max = Math.max(...chartData);
    const labels = chartData.map((_, i) => 'Day ' + (i + 1));
    window.activityChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Messages',
                data: chartData,
                backgroundColor: 'rgba(79, 70, 229, 0.55)',
                borderColor: 'rgba(79, 70, 229, 1)',
                borderWidth: 1,
                borderRadius: 5,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, ticks: { precision: 0 } },
                x: { grid: { display: false } }
            }
        }
    });
}

// User Management
async function loadUsers() {
    try {
        const usersDataRes = await fetchAPI('/admin-users/');
        usersData = usersDataRes.users || usersDataRes;
        filteredUsers = [...usersData];
        currentUserPage = 1;
        renderUsers();
    } catch (error) {
        console.error('Failed to load users:', error);
    }
}

function filterUsers() {
    const search = document.getElementById('searchUsers').value.toLowerCase();
    const status = document.getElementById('filterStatus').value;
    
    filteredUsers = usersData.filter(user => {
        const matchesSearch = user.username.toLowerCase().includes(search) || 
                            user.email.toLowerCase().includes(search);
        const matchesStatus = status === 'all' || user.status === status;
        return matchesSearch && matchesStatus;
    });
    
    currentUserPage = 1;
    renderUsers();
}

function renderUsers() {
    const tbody = document.getElementById('usersTableBody');
    const start = (currentUserPage - 1) * usersPerPage;
    const end = start + usersPerPage;
    const pageUsers = filteredUsers.slice(start, end);
    
    if (pageUsers.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="loading">No users found</td></tr>';
        return;
    }
    
    tbody.innerHTML = pageUsers.map(user => `
        <tr>
            <td>${user.id}</td>
            <td>${user.username}</td>
            <td>${user.email}</td>
            <td><span class="status-badge status-${user.status}">${user.status}</span></td>
            <td>${formatDate(user.lastActive)}</td>
            <td>
                ${user.status === 'active' 
                    ? `<button class="btn btn-warning btn-sm" onclick="banUser(${user.id})">Ban</button>`
                    : `<button class="btn btn-primary btn-sm" onclick="unbanUser(${user.id})">Unban</button>`
                }
                <button class="btn btn-danger btn-sm" onclick="deleteUser(${user.id})">Delete</button>
            </td>
        </tr>
    `).join('');
    
    // Hide skeleton rows
    tbody.querySelectorAll('.skeleton-row').forEach(r => r.remove());
    
    renderPagination();
}

function renderPagination() {
    const totalPages = Math.ceil(filteredUsers.length / usersPerPage);
    const pagination = document.getElementById('usersPagination');
    
    let html = `<button ${currentUserPage === 1 ? 'disabled' : ''} onclick="changePage(${currentUserPage - 1})">Previous</button>`;
    
    for (let i = 1; i <= totalPages; i++) {
        if (i === 1 || i === totalPages || (i >= currentUserPage - 1 && i <= currentUserPage + 1)) {
            html += `<button class="${i === currentUserPage ? 'active' : ''}" onclick="changePage(${i})">${i}</button>`;
        } else if (i === currentUserPage - 2 || i === currentUserPage + 2) {
            html += '<button disabled>...</button>';
        }
    }
    
    html += `<button ${currentUserPage === totalPages ? 'disabled' : ''} onclick="changePage(${currentUserPage + 1})">Next</button>`;
    pagination.innerHTML = html;
}

function changePage(page) {
    currentUserPage = page;
    renderUsers();
}

function banUser(userId) {
    showConfirm('Ban User', 'Are you sure you want to ban this user?', async () => {
        try {
            await fetchAPI('/admin-users/toggle/', { method: 'POST', body: JSON.stringify({ user_id: userId, is_active: false }) });
            const user = usersData.find(u => u.id === userId);
            if (user) user.is_active = false;
            renderUsers();
            showToast('User banned successfully', 'success');
        } catch (error) {
            showToast('Failed to ban user', 'error');
        }
    });
}

function unbanUser(userId) {
    showConfirm('Unban User', 'Are you sure you want to unban this user?', async () => {
        try {
            await fetchAPI('/admin-users/toggle/', { method: 'POST', body: JSON.stringify({ user_id: userId, is_active: true }) });
            const user = usersData.find(u => u.id === userId);
            if (user) user.is_active = true;
            renderUsers();
            showToast('User unbanned successfully', 'success');
        } catch (error) {
            showToast('Failed to unban user', 'error');
        }
    });
}

function deleteUser(userId) {
    showConfirm('Delete User', 'This action cannot be undone. Delete this user?', async () => {
        try {
            await fetchAPI('/admin-users/delete/', { method: 'POST', body: JSON.stringify({ user_id: userId }) });
            usersData = usersData.filter(u => u.id !== userId);
            filteredUsers = filteredUsers.filter(u => u.id !== userId);
            renderUsers();
            showToast('User deleted successfully', 'success');
        } catch (error) {
            showToast('Failed to delete user', 'error');
        }
    });
}

// Chat Monitoring
async function loadChats() {
    try {
        const conversations = await fetchAPI('/admin-online-users/');
        const convList = (conversations.sessions || []).map(s => ({
            id: s.user,
            username: s.user,
            lastMessage: 'Active',
            unread: 0
        }));
        renderConversations(convList);
    } catch (error) {
        console.error('Failed to load chats:', error);
    }
}

function renderConversations(conversations) {
    const list = document.getElementById('conversationsList');
    list.innerHTML = conversations.map(conv => `
        <div class="conversation-item" onclick="loadChatMessages(${conv.id}, '${conv.username}')">
            <strong>${conv.username}</strong>
            <small>${conv.lastMessage || 'Active'}</small>
            ${conv.unread > 0 ? `<span class="status-badge status-active">${conv.unread} new</span>` : ''}
        </div>
    `).join('');
    // Hide skeleton rows
    list.querySelectorAll('.skeleton-row').forEach(r => r.remove());
}

async function loadChatMessages(userId, username) {
    document.getElementById('chatHeader').textContent = `Chat with ${username}`;
    
    try {
        const messages = await fetchAPI('/admin-online-users/');
        const msgs = [];
        container.innerHTML = '<p class="empty-state">Chat history loading...</p>';
    } catch (error) {
        container.innerHTML = '<p class="empty-state">Chat history unavailable</p>';
    }
}

// Reports
async function loadReports() {
    try {
        const reports = await fetchAPI('/admin-audit-log/?action=ban');
        const data = reports.logs || [];
        const formatted = data.map(r => ({
            id: r.id,
            reporter: 'System',
            reportedUser: r.target,
            reason: r.detail || r.action,
            timestamp: r.time
        }));
        renderReports(formatted);
    } catch (error) {
        console.error('Failed to load reports:', error);
    }
}

function renderReports(reports) {
    const container = document.getElementById('reportsContainer');
    
    if (reports.length === 0) {
        container.innerHTML = '<p class="loading">No reports found</p>';
        return;
    }
    
    container.innerHTML = reports.map(report => `
        <div class="report-card">
            <div class="report-header">
                <div class="report-info">
                    <strong>Reported: ${report.reportedUser}</strong>
                    <small>By ${report.reporter} • ${formatDate(report.timestamp)}</small>
                </div>
            </div>
            <div class="report-reason">${report.reason}</div>
            <div class="report-actions">
                <button class="btn btn-secondary btn-sm" onclick="ignoreReport(${report.id})">Ignore</button>
                <button class="btn btn-warning btn-sm" onclick="warnUser('${report.reportedUser}')">Warn</button>
                <button class="btn btn-danger btn-sm" onclick="banReportedUser('${report.reportedUser}')">Ban User</button>
            </div>
        </div>
    `).join('');
    // Hide skeleton rows
    container.querySelectorAll('.skeleton').forEach(r => r.remove());
}

function ignoreReport(reportId) {
    showConfirm('Ignore Report', 'Mark this report as resolved?', async () => {
        try {
            await fetchAPI(`/reports/${reportId}/ignore/`, { method: 'POST' });
            showToast('Report ignored', 'success');
            loadReports();
        } catch (error) {
            showToast('Failed to ignore report', 'error');
        }
    });
}

function warnUser(username) {
    showConfirm('Warn User', `Send a warning to ${username}?`, async () => {
        try {
            showToast('Warning sent', 'success');
        } catch (error) {
            showToast('Failed to send warning', 'error');
        }
    });
}

function banReportedUser(username) {
    showConfirm('Ban User', `Ban ${username} permanently?`, async () => {
        try {
            showToast('User banned', 'success');
            loadReports();
        } catch (error) {
            showToast('Failed to ban user', 'error');
        }
    });
}

// System Controls
function loadSystem() {
    // Load current settings if needed
}

function toggleMaintenance(e) {
    const isEnabled = e.target.checked;
    document.getElementById('maintenanceStatus').textContent = isEnabled ? 'ON' : 'OFF';
    
    fetchAPI('/admin-maintenance/', {
        method: 'POST',
        body: JSON.stringify({ enabled: isEnabled })
    }).then(() => {
        showToast(`Maintenance mode ${isEnabled ? 'enabled' : 'disabled'}`, 'success');
    }).catch(() => {
        showToast('Failed to update maintenance mode', 'error');
        e.target.checked = !isEnabled;
    });
}

function sendBroadcast() {
    const message = document.getElementById('broadcastMessage').value.trim();
    
    if (!message) {
        showToast('Please enter a message', 'error');
        return;
    }
    
    showConfirm('Send Broadcast', 'Send this message to all users?', async () => {
        try {
            await fetchAPI('/admin-announce/', {
                method: 'POST',
                body: JSON.stringify({ text: message })
            });
            document.getElementById('broadcastMessage').value = '';
            showToast('Broadcast sent successfully', 'success');
        } catch (error) {
            showToast('Failed to send broadcast', 'error');
        }
    });
}

// Utilities
function formatDate(dateString) {
    const date = new Date(dateString);
    const now = new Date();
    const diff = now - date;
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);
    
    if (minutes < 1) return 'Just now';
    if (minutes < 60) return `${minutes}m ago`;
    if (hours < 24) return `${hours}h ago`;
    if (days < 7) return `${days}d ago`;
    return date.toLocaleDateString();
}

function showConfirm(title, message, callback) {
    document.getElementById('modalTitle').textContent = title;
    document.getElementById('modalMessage').textContent = message;
    confirmCallback = callback;
    document.getElementById('confirmModal').classList.add('active');
}

function closeModal() {
    document.getElementById('confirmModal').classList.remove('active');
    confirmCallback = null;
}

function showToast(message, type = 'success') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = `toast ${type} show`;
    
    setTimeout(() => {
        toast.classList.remove('show');
    }, 3000);
}

/* ---------- Dark mode toggle ---------- */
function toggleDarkMode() {
    const isDark = document.body.classList.toggle('dark-theme');
    localStorage.setItem('adminDarkMode', isDark ? '1' : '0');
    const toggleBtn = document.getElementById('themeToggle');
    if (toggleBtn) toggleBtn.textContent = isDark ? '☀️' : '🌙';
}

// Load saved theme preference
if (localStorage.getItem('adminDarkMode') === '1') {
    document.body.classList.add('dark-theme');
    const toggleBtn = document.getElementById('themeToggle');
    if (toggleBtn) toggleBtn.textContent = '☀️';
}

// Resize chart on window resize (Chart.js is responsive, this is a fallback)
window.addEventListener('resize', () => {
    if (currentPage === 'dashboard' && window.lastChartData) {
        renderChart(window.lastChartData);
    }
});
