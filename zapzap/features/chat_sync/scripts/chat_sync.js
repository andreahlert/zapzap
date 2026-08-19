// zapzap/features/chat_sync/scripts/chat_sync.js
// Runs in the page MainWorld after window.WPP is injected.
// Live messages buffer in _pending; the engine drains them ~1s.
(function () {
    if (window._zapzapSync) {
        return;
    }

    var state = { status: 'idle', result: null, error: null };
    var pending = [];
    var live = false;
    var meLabel = 'Me';
    var titles = {};

    function fail(err) {
        state.error = String((err && err.message) || err);
        state.status = 'error';
    }

    function widToString(wid) {
        if (!wid) { return ''; }
        if (typeof wid === 'string') { return wid; }
        return wid._serialized || '';
    }

    function chatTitle(chat) {
        if (!chat) { return ''; }
        var contact = chat.contact || {};
        return chat.formattedTitle || chat.name || contact.formattedName ||
            contact.pushname || widToString(chat.id);
    }

    var MEDIA_TYPES = {
        image: 1, video: 1, audio: 1, ptt: 1,
        document: 1, sticker: 1, gif: 1,
    };

    function normalize(message) {
        var fromMe = !!(message.fromMe ||
            (message.id && message.id.fromMe));
        var chatId = widToString(message.from) || widToString(
            message.id && message.id.remote);
        var senderId = widToString(message.author) ||
            widToString(message.from);
        return {
            id: widToString(message.id),
            chat_id: chatId,
            chat_name: titles[chatId] || '',
            sender_id: senderId,
            sender_name: fromMe ? meLabel : (message.notifyName || ''),
            ts: message.t || 0,
            type: message.type || 'chat',
            // WhatsApp puts base64 thumbnail/media data in `body` for media
            // messages; keep only real text (chat types). Captions are stored
            // separately below and media bytes are fetched via downloadMedia.
            body: (!MEDIA_TYPES[message.type] &&
                typeof message.body === 'string') ? message.body : '',
            caption: typeof message.caption === 'string'
                ? message.caption : '',
            from_me: fromMe ? 1 : 0,
            mimetype: typeof message.mimetype === 'string'
                ? message.mimetype : '',
            filename: typeof message.filename === 'string'
                ? message.filename : '',
        };
    }

    window._zapzapSync = {
        isEngineReady: function () {
            try {
                return typeof window.WPP !== 'undefined' &&
                    !!(window.WPP.isFullReady || window.WPP.isReady);
            } catch (e) { return false; }
        },

        listChats: function () {
            state.status = 'working';
            state.result = null;
            state.error = null;
            WPP.chat.list()
                .then(function (chats) {
                    state.result = chats.map(function (chat) {
                        var id = widToString(chat.id);
                        var name = chatTitle(chat);
                        titles[id] = name;
                        return { id: id, name: name,
                                 isGroup: !!chat.isGroup };
                    });
                    state.status = 'done';
                })
                .catch(fail);
        },

        getMessagesSince: function (chatId, pageSize, beforeId) {
            state.status = 'working';
            state.result = null;
            state.error = null;
            var opts = { count: pageSize };
            if (beforeId) {
                opts.id = beforeId;
                opts.direction = 'before';
            }
            WPP.chat.getMessages(chatId, opts)
                .then(function (messages) {
                    var rows = messages.map(normalize);
                    // WA-JS returns oldest-first; the engine wants newest-first.
                    rows.reverse();
                    for (var i = 0; i < rows.length; i++) {
                        if (!rows[i].chat_name) {
                            rows[i].chat_name = titles[chatId] || '';
                        }
                        rows[i].chat_id = rows[i].chat_id || chatId;
                    }
                    state.result = rows;
                    state.status = 'done';
                })
                .catch(fail);
        },

        startLive: function (label) {
            if (label) { meLabel = label; }
            if (live) { return true; }
            try {
                WPP.on('chat.new_message', function (message) {
                    try { pending.push(normalize(message)); }
                    catch (e) { /* skip a malformed event */ }
                });
                live = true;
                return true;
            } catch (e) {
                return false;
            }
        },

        drainPending: function () {
            var out = pending;
            pending = [];
            return out;
        },

        downloadMedia: function (messageId) {
            state.status = 'working';
            state.result = null;
            state.error = null;
            WPP.chat.downloadMedia(messageId)
                .then(WPP.util.blobToBase64)
                .then(function (dataUrl) {
                    state.result = dataUrl || '';
                    state.status = 'done';
                })
                .catch(fail);
        },

        poll: function () {
            if (state.status === 'done') {
                var done = { status: 'done', result: state.result };
                state.status = 'idle';
                state.result = null;
                return done;
            }
            if (state.status === 'error') {
                var error = { status: 'error', error: state.error };
                state.status = 'idle';
                state.error = null;
                return error;
            }
            return { status: state.status };
        },
    };
})();
