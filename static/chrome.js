/* 状态栏同步：视图名与字符数跟随主区域 */
(function () {
    function sync() {
        var active = document.querySelector('.view-tab.is-active');
        var stats = document.getElementById('resultStats');
        var view = document.getElementById('footView');
        var chars = document.getElementById('footChars');
        if (view && active) {
            view.textContent = active.dataset.view === 'summary' ? '摘要'
                : active.dataset.view === 'notes' ? '笔记' : '原文';
        }
        if (chars && stats) {
            var n = (stats.textContent || '').match(/\d+/);
            chars.textContent = n ? n[0] : '0';
        }
    }
    document.addEventListener('DOMContentLoaded', function () {
        sync();
        var stats = document.getElementById('resultStats');
        if (stats) {
            new MutationObserver(sync).observe(stats, { childList: true, characterData: true, subtree: true });
        }
        document.querySelectorAll('.view-tab').forEach(function (t) {
            t.addEventListener('click', function () { setTimeout(sync, 30); });
        });
    });
})();
