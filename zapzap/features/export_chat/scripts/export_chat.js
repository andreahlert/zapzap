// ZapZap chat export helpers.
// Runs in the page MainWorld after the WA-JS bundle (window.WPP) is injected.
// Tasks run asynchronously and publish their outcome through poll(), because
// QWebEnginePage.runJavaScript() cannot await page-side promises.
(function () {
    if (window._zapzapExport) {
        return;
    }

    var state = {
        status: 'idle', // idle | working | done | error
        result: null,
        error: null,
    };

    function fail(err) {
        state.error = String((err && err.message) || err);
        state.status = 'error';
    }

    function widToString(wid) {
        if (!wid) {
            return '';
        }
        if (typeof wid === 'string') {
            return wid;
        }
        return wid._serialized || '';
    }

    window._zapzapExport = {
        isEngineReady: function () {
            try {
                return typeof window.WPP !== 'undefined' &&
                    !!(window.WPP.isFullReady || window.WPP.isReady);
            } catch (e) {
                return false;
            }
        },

        listChats: function () {
            state.status = 'working';
            state.result = null;
            state.error = null;

            WPP.chat.list()
                .then(function (chats) {
                    state.result = chats.map(function (chat) {
                        var contact = chat.contact || {};
                        return {
                            id: widToString(chat.id),
                            name: chat.formattedTitle || chat.name ||
                                contact.formattedName || contact.pushname ||
                                widToString(chat.id),
                            isGroup: !!chat.isGroup,
                        };
                    });
                    state.status = 'done';
                })
                .catch(fail);
        },

        exportMessages: function (chatId, count, meLabel) {
            state.status = 'working';
            state.result = null;
            state.error = null;

            var nameCache = {};

            function contactName(id) {
                if (!id) {
                    return Promise.resolve('');
                }
                if (nameCache[id]) {
                    return Promise.resolve(nameCache[id]);
                }
                var fallback = id.split('@')[0];
                return WPP.contact.get(id)
                    .then(function (contact) {
                        var name = (contact && (contact.formattedName ||
                            contact.pushname || contact.name)) || fallback;
                        nameCache[id] = name;
                        return name;
                    })
                    .catch(function () {
                        nameCache[id] = fallback;
                        return fallback;
                    });
            }

            (async function () {
                var me = meLabel || 'Me';
                try {
                    var myId = widToString(WPP.conn.getMyUserId());
                    var resolved = await contactName(myId);
                    if (resolved) {
                        me = resolved;
                    }
                } catch (e) {
                    // Keep the label provided by the application.
                }

                var messages = await WPP.chat.getMessages(
                    chatId, { count: count });
                var rows = [];
                for (var i = 0; i < messages.length; i++) {
                    var message = messages[i];
                    var fromMe = !!(message.fromMe ||
                        (message.id && message.id.fromMe));
                    var senderId = widToString(message.author) ||
                        widToString(message.from);
                    var sender = fromMe
                        ? me
                        : (await contactName(senderId)) ||
                            message.notifyName || '';
                    rows.push({
                        id: widToString(message.id),
                        t: message.t || 0,
                        sender: sender,
                        type: message.type || 'chat',
                        body: typeof message.body === 'string'
                            ? message.body : '',
                        caption: typeof message.caption === 'string'
                            ? message.caption : '',
                        mimetype: typeof message.mimetype === 'string'
                            ? message.mimetype : '',
                        filename: typeof message.filename === 'string'
                            ? message.filename : '',
                    });
                }
                return rows;
            })()
                .then(function (rows) {
                    state.result = rows;
                    state.status = 'done';
                })
                .catch(fail);
        },

        downloadMedia: function (messageId) {
            state.status = 'working';
            state.result = null;
            state.error = null;

            WPP.chat.downloadMedia(messageId)
                .then(WPP.util.blobToBase64)
                .then(function (dataUrl) {
                    // blobToBase64 yields a "data:<mime>;base64,<payload>" URL.
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
