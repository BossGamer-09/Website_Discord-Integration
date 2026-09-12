class AuthManager {
    constructor() {
        this.apiBase = '/api';
        this.isAuthenticated = false;
        this.userData = null;
        this.csrfToken = null;
        
        // Initialize CSRF token
        this.initializeCSRF();
    }
    
    initializeCSRF() {
        // Get CSRF token from meta tag if exists
        const csrfMeta = document.querySelector('meta[name="csrf-token"]');
        if (csrfMeta) {
            this.csrfToken = csrfMeta.getAttribute('content');
        }
        
        // Or generate one
        if (!this.csrfToken) {
            this.csrfToken = this.generateCSRFToken();
        }
    }
    
    generateCSRFToken() {
        const array = new Uint8Array(32);
        window.crypto.getRandomValues(array);
        return Array.from(array, byte => byte.toString(16).padStart(2, '0')).join('');
    }
    
    async checkAuth() {
        try {
            const response = await this.apiRequest('/user');
            this.isAuthenticated = response.authenticated;
            this.userData = response.user;
            return this.isAuthenticated;
        } catch (error) {
            this.isAuthenticated = false;
            this.userData = null;
            return false;
        }
    }
    
    async loginWithDiscord() {
        window.location.href = '/user/discord/login/';
    }

    async logout() {
        try {
            await fetch('/user/logout/', {
                method: 'POST',
                credentials: 'include',
                headers: { 'X-CSRFToken': this.csrfToken }
            });
        } catch (error) {
            console.error('Logout error:', error);
        } finally {
            this.isAuthenticated = false;
            this.userData = null;
            window.location.href = '/';
        }
    }
    
    async getUserGuilds() {
        try {
            return await this.apiRequest('/user/guilds');
        } catch (error) {
            console.error('Failed to get user guilds:', error);
            return { guilds: [], is_member: false };
        }
    }
    
    async getUserRoles() {
        try {
            return await this.apiRequest('/user/roles');
        } catch (error) {
            console.error('Failed to get user roles:', error);
            return { roles: [], nickname: '', avatar: null };
        }
    }
    
    async apiRequest(endpoint, options = {}) {
        const url = `${this.apiBase}${endpoint}`;
        const defaultOptions = {
            credentials: 'include',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': this.csrfToken
            },
            cache: 'no-store' // Prevent caching of sensitive data
        };
        
        const response = await fetch(url, { ...defaultOptions, ...options });
        
        if (!response.ok) {
            if (response.status === 401) {
                this.isAuthenticated = false;
                this.userData = null;
            }
            throw new Error(`API request failed: ${response.statusText}`);
        }
        
        return await response.json();
    }
    
    // Role display helpers
    formatRoleName(role) {
        if (role.name.toLowerCase().includes('admin')) return 'Administrator';
        if (role.name.toLowerCase().includes('mod')) return 'Moderator';
        if (role.name.toLowerCase().includes('officer')) return 'Officer';
        if (role.name.toLowerCase().includes('member')) return 'Member';
        return role.name;
    }
    
    getRoleClass(role) {
        const name = role.name.toLowerCase();
        if (name.includes('admin')) return 'admin';
        if (name.includes('mod')) return 'moderator';
        if (name.includes('officer')) return 'officer';
        if (name.includes('member')) return 'member';
        return '';
    }
    
    // Update UI based on auth state
    updateAuthUI() {
        const authElements = document.querySelectorAll('[data-auth]');
        const guestElements = document.querySelectorAll('[data-guest]');
        const userElements = document.querySelectorAll('[data-user]');
        
        if (this.isAuthenticated && this.userData) {
            // Show authenticated content
            authElements.forEach(el => {
                if (el.dataset.auth === 'show') el.style.display = '';
                if (el.dataset.auth === 'hide') el.style.display = 'none';
            });
            
            // Update user info
            userElements.forEach(el => {
                const attr = el.dataset.user;
                if (attr === 'username') el.textContent = this.userData.username;
                if (attr === 'avatar' && this.userData.avatar) {
                    el.src = this.userData.avatar;
                    el.style.display = '';
                }
            });
            
            // Hide guest content
            guestElements.forEach(el => {
                if (el.dataset.guest === 'show') el.style.display = 'none';
                if (el.dataset.guest === 'hide') el.style.display = '';
            });
        } else {
            // Show guest content
            authElements.forEach(el => {
                if (el.dataset.auth === 'show') el.style.display = 'none';
                if (el.dataset.auth === 'hide') el.style.display = '';
            });
            
            guestElements.forEach(el => {
                if (el.dataset.guest === 'show') el.style.display = '';
                if (el.dataset.guest === 'hide') el.style.display = 'none';
            });
        }
    }
}

// Initialize auth manager globally
window.authManager = new AuthManager();

// Performance-optimized DOMContentLoaded
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeApp);
} else {
    initializeApp();
}

async function initializeApp() {
    try {
        // Check authentication
        const isAuthenticated = await window.authManager.checkAuth();
        
        // Update UI
        window.authManager.updateAuthUI();
        
        // If authenticated, update the portal link to go straight to the portal
        const membersLink = document.getElementById('members-link');
        const footerMembersLink = document.getElementById('footer-members-link');
        if (isAuthenticated) {
            if (membersLink) membersLink.href = '/user/index/';
            if (footerMembersLink) footerMembersLink.href = '/user/index/';
        }
        
        // Setup event listeners
        setupEventListeners();
        
    } catch (error) {
        console.error('App initialization error:', error);
    }
}

async function loadMemberData() {
    try {
        // Show loading state
        const content = document.getElementById('member-content');
        if (content) {
            content.innerHTML = '<div class="loading"></div>';
        }
        
        // Load user guilds and roles in parallel
        const [guildsData, rolesData] = await Promise.all([
            window.authManager.getUserGuilds(),
            window.authManager.getUserRoles()
        ]);
        
        // Update UI with loaded data
        updateMemberUI(guildsData, rolesData);
        
    } catch (error) {
        console.error('Failed to load member data:', error);
        showError('Failed to load member data. Please try again.');
    }
}

function updateMemberUI(guildsData, rolesData) {
    const content = document.getElementById('member-content');
    if (!content) return;
    
    let html = `
        <div class="user-profile fade-in">
            <div class="profile-header">
                <img src="${rolesData.avatar || '/images/default-avatar.png'}" 
                     alt="Avatar" 
                     class="avatar">
                <div class="user-info">
                    <h2>${rolesData.nickname || window.authManager.userData.username}</h2>
                    <div class="username">@${window.authManager.userData.username}</div>
                </div>
            </div>
            
            <div class="roles-container">
                <h3 class="roles-title">Your Roles</h3>
    `;
    
    if (rolesData.roles && rolesData.roles.length > 0) {
        html += '<div class="roles-grid">';
        // Sort roles by position (highest first)
        const sortedRoles = [...rolesData.roles].sort((a, b) => b.position - a.position);
        
        sortedRoles.forEach(role => {
            const roleClass = window.authManager.getRoleClass(role);
            const roleName = window.authManager.formatRoleName(role);
            html += `
                <div class="role-badge ${roleClass}">
                    <span class="role-dot" style="background-color: #${role.color.toString(16).padStart(6, '0')}"></span>
                    ${roleName}
                </div>
            `;
        });
        html += '</div>';
    } else if (rolesData.error) {
        html += `<p class="text-muted">${rolesData.error}</p>`;
    } else {
        html += '<p class="text-muted">No roles found.</p>';
    }
    
    html += `
            </div>
            
            <div class="guild-info mt-3">
                <h3 class="roles-title">Guild Membership</h3>
    `;
    
    if (guildsData.is_member) {
        html += `
            <p>✅ You are a member of the Blightveil Discord server.</p>
            <p class="text-muted">Access level: ${guildsData.blightveil_guild ? (guildsData.blightveil_guild.owner ? 'Owner' : 'Member') : 'Unknown'}</p>
        `;
    } else {
        html += `
            <p>❌ You are not a member of the Blightveil Discord server.</p>
            <a href="https://discord.gg/blightveil" target="_blank" class="discord-btn mt-2" style="display: inline-block; padding: 0.8rem 2rem;">
                Join Discord
            </a>
        `;
    }
    
    html += `
            </div>
        </div>
        
        <div class="text-center mt-3">
            <button onclick="window.authManager.logout()" class="auth-btn" style="background: var(--danger);">
                Logout
            </button>
        </div>
    `;
    
    content.innerHTML = html;
    
    // Add CSS for role dot
    const style = document.createElement('style');
    style.textContent = `
        .role-dot {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 6px;
        }
    `;
    document.head.appendChild(style);
}

function showError(message) {
    const content = document.getElementById('member-content');
    if (content) {
        content.innerHTML = `
            <div class="error-message" style="text-align: center; padding: 2rem; color: var(--accent);">
                <p>${message}</p>
                <button onclick="loadMemberData()" class="auth-btn mt-2">
                    Retry
                </button>
            </div>
        `;
    }
}

function setupEventListeners() {
    // Discord login button
    const discordLoginBtn = document.getElementById('discord-login');
    if (discordLoginBtn) {
        discordLoginBtn.addEventListener('click', (e) => {
            e.preventDefault();
            window.authManager.loginWithDiscord();
        });
    }
    
    // Logout button
    const logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', (e) => {
            e.preventDefault();
            window.authManager.logout();
        });
    }
    
    // Members link
    const membersLink = document.getElementById('members-link');
    if (membersLink) {
        membersLink.addEventListener('click', (e) => {
            if (!window.authManager.isAuthenticated) {
                e.preventDefault();
                window.authManager.loginWithDiscord();
            }
        });
    }
}