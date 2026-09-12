
// Bot status check
async function checkBotStatus() {
    try {
        const response = await fetch('/api/status');
        const data = await response.json();
        const statusEl = document.getElementById('bot-status');
        if (statusEl) {
            statusEl.textContent = `Bot Status: ${data.bot.ready ? 'Online' : 'Offline'}`;
            statusEl.style.color = data.bot.ready ? '#43b581' : '#f04747';
        }
    } catch (error) {
        const statusEl = document.getElementById('bot-status');
        if (statusEl) {
            statusEl.textContent = 'Bot Status: Error';
            statusEl.style.color = '#f04747';
        }
    }
}

// Setup login button
function setupLoginButton() {
    const loginBtn = document.getElementById('discord-login');
    if (loginBtn) {
        loginBtn.addEventListener('click', function(e) {
            e.preventDefault();
            window.location.href = '/user/discord/login/';
        });
    }
}

// Setup logout button
function setupLogoutButton() {
    const logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', function(e) {
            e.preventDefault();
            fetch('/user/logout/', {
                method: 'POST',
                credentials: 'include'
            }).then(() => {
                window.location.href = '/';
            });
        });
    }
}

// Initialize
document.addEventListener('DOMContentLoaded', function() {
    checkBotStatus();
    setupLoginButton();
    setupLogoutButton();
});