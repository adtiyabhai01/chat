    let currentUserId = null;

    // DOM Elements (safe init)
    let messagesContainer, contactName, contactAvatar, messageInput, sendBtn;


    document.addEventListener("DOMContentLoaded", function () {
        console.log("JS Loaded");

        // Initialize elements
        messagesContainer = document.getElementById('messagesContainer');
        contactName = document.getElementById('contactName');
        contactAvatar = document.getElementById('contactAvatar');
        messageInput = document.getElementById('messageInput');
        sendBtn = document.getElementById('sendBtn');


        if (messageInput) {
            messageInput.addEventListener('keypress', function (e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    sendMessage();
                }
            });
        }


        setupMobileUI();
    });



    function getCookie(name) {
        const cookies = document.cookie.split(';');
        for (let cookie of cookies) {
            cookie = cookie.trim();
            if (cookie.startsWith(name + '=')) {
                return cookie.substring(name.length + 1);
            }
        }
        return null;
    }



    function openChat(userId, username, imageUrl) {
        console.log("Opening chat with:", userId);

        currentUserId = userId;

        if (contactName) contactName.innerText = username;
        if (contactAvatar) contactAvatar.src = imageUrl;
        if (messageInput) messageInput.disabled = false;
        if (sendBtn) sendBtn.disabled = false;

        if (messagesContainer) messagesContainer.innerHTML = "";

        loadMessages(userId);

        // Mobile UX
        if (typeof closeSidebarOnMobile === "function") {
            closeSidebarOnMobile();
        }
    }



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
                appendMessage(message, "sent");
                messageInput.value = "";
            } else {
                console.error("Server error:", data);
            }
        })
        .catch(err => console.error("Send error:", err));
    }


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



    function appendMessage(text, type) {
        if (!messagesContainer) return;

        const div = document.createElement("div");
        div.className = `message ${type}`;
        div.innerText = text;

        messagesContainer.appendChild(div);

        // Auto scroll
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }



    function setupMobileUI() {
        const mobileBackBtn = document.getElementById('mobileBackBtn');
        const sidebar = document.getElementById('sidebar');
        const newChatBtn = document.getElementById('newChatBtn');

        if (mobileBackBtn && sidebar) {
            mobileBackBtn.addEventListener('click', () => {
                sidebar.classList.remove('active');
            });
        }

        if (newChatBtn && sidebar) {
            newChatBtn.addEventListener('click', () => {
                if (window.innerWidth <= 768) {
                    sidebar.classList.add('active');
                }
            });
        }

        // Global function
        window.closeSidebarOnMobile = function () {
            if (window.innerWidth <= 768 && sidebar) {
                sidebar.classList.remove('active');
            }
        };
    }

    function loadUsers() {
        fetch('/users-with-unread/')
        .then(res => res.json())
        .then(data => {
            const userList = document.getElementById("userList");
            userList.innerHTML = "";

            data.users.forEach(user => {

                const div = document.createElement("div");
                div.className = "chat-user";

                // username
                const name = document.createElement("span");
                name.innerText = user.username;

                // unread badge
                const badge = document.createElement("span");
                badge.className = "badge";

                if (user.unread > 0) {
                    badge.innerText = user.unread;
                } else {
                    badge.style.display = "none";
                }

                div.appendChild(name);
                div.appendChild(badge);

                // click → open chat
                div.onclick = () => openChat(user.id, user.username, "");

                userList.appendChild(div);
            });
        });
    }