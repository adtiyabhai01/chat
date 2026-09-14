<script>
const CURRENT_USER_ID = {{ user.id }};

let currentUserId = null;
let chatInterval = null;

// Elements
let messagesContainer, contactName, contactAvatar, messageInput, sendBtn;

document.addEventListener("DOMContentLoaded", () => {
    console.log("Chat Loaded ✅");

    messagesContainer = document.getElementById('messagesContainer');
    contactName = document.getElementById('contactName');
    contactAvatar = document.getElementById('contactAvatar');
    messageInput = document.getElementById('messageInput');
    sendBtn = document.querySelector('.send-btn');

    if (messageInput) {
        messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                sendMessage();
            }
        });
    }

    setupMobileUI();
});


// ✅ GLOBAL FUNCTION (FIXED)
window.openChat = function(userId, username = "User", imageUrl = "") {
    console.log("Opening chat:", userId);

    currentUserId = userId;

    if (contactName) contactName.innerText = username;
    if (contactAvatar) contactAvatar.src = imageUrl;
    if (messageInput) messageInput.disabled = false;
    if (sendBtn) sendBtn.disabled = false;

    if (messagesContainer) messagesContainer.innerHTML = "";

    loadMessages(userId);

    // clear old interval
    if (chatInterval) clearInterval(chatInterval);

    // auto refresh
    chatInterval = setInterval(() => {
        if (currentUserId) loadMessages(currentUserId);
    }, 2000);

    closeSidebarOnMobile();
};


// ✅ SEND MESSAGE (FIXED)
function sendMessage() {
    const message = messageInput?.value?.trim();
    if (!message || !currentUserId) return;

    fetch('/send-message/', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken')
        },
        body: JSON.stringify({
            receiver_id: currentUserId,
            content: message
        })
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === "success") {
            messageInput.value = "";
            loadMessages(currentUserId); // 🔥 instant refresh
        }
    })
    .catch(err => console.error("Send error:", err));
}


// LOAD MESSAGES
function loadMessages(userId) {
    fetch(`/get-messages/${userId}/`)
    .then(res => res.json())
    .then(data => {
        if (!messagesContainer) return;

        messagesContainer.innerHTML = "";

        const messages = data.messages || data;

        messages.forEach(msg => {
            const type = (msg.sender == CURRENT_USER_ID) ? "sent" : "received";
            const text = msg.message || msg.text || "";

            appendMessage(text, type);
        });
    })
    .catch(err => console.error("Load error:", err));
}


// APPEND MESSAGE
function appendMessage(text, type) {
    if (!messagesContainer) return;

    const div = document.createElement("div");
    div.className = `message ${type}`;
    div.innerText = text;

    messagesContainer.appendChild(div);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}


// COOKIE
function getCookie(name) {
    return document.cookie
        .split('; ')
        .find(row => row.startsWith(name + '='))
        ?.split('=')[1];
}


// MOBILE UI
function setupMobileUI() {
    const sidebar = document.getElementById('sidebar');
    const mobileBackBtn = document.getElementById('mobileBackBtn');
    const overlay = document.getElementById('sidebarOverlay');

    if (mobileBackBtn && sidebar) {
        mobileBackBtn.addEventListener('click', () => {
            sidebar.classList.remove('active');
            if (overlay) overlay.style.display = 'none';
        });
    }

    window.closeSidebarOnMobile = function () {
        if (window.innerWidth <= 768 && sidebar) {
            sidebar.classList.remove('active');
            if (overlay) overlay.style.display = 'none';
        }
    };

    window.addEventListener('resize', () => {
        if (window.innerWidth > 768) {
            sidebar?.classList.remove('active');
            if (overlay) overlay.style.display = 'none';
        }
    });
}
</script>