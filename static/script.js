document.addEventListener('DOMContentLoaded', function() {
    const configuredMaxBytes = Number.parseInt(document.body.dataset.maxUploadBytes, 10);
    const configuredMaxMb = Number.parseInt(document.body.dataset.maxUploadMb, 10);
    const MAX_UPLOAD_BYTES = Number.isSafeInteger(configuredMaxBytes) && configuredMaxBytes > 0
        ? configuredMaxBytes
        : 100 * 1024 * 1024;
    const MAX_UPLOAD_MB = Number.isSafeInteger(configuredMaxMb) && configuredMaxMb > 0
        ? configuredMaxMb
        : Math.ceil(MAX_UPLOAD_BYTES / (1024 * 1024));
    const configuredMaxTextChars = Number.parseInt(document.body.dataset.maxTextChars, 10);
    const MAX_TEXT_CHARS = Number.isSafeInteger(configuredMaxTextChars) && configuredMaxTextChars > 0
        ? configuredMaxTextChars
        : 250000;

    const transcriptionForm = document.getElementById('transcriptionForm');
    const audioFileInput = document.getElementById('audioFile');
    const audioFileLabel = document.getElementById('audioFileLabel');
    const fileSummary = document.getElementById('fileSummary');
    const fileNameDisplay = document.getElementById('fileNameDisplay');
    const fileMetaDisplay = document.getElementById('fileMetaDisplay');
    const submitBtn = document.getElementById('submitBtn');
    const statusMessage = document.getElementById('statusMessage');
    const workspaceStatus = document.getElementById('workspaceStatus');
    const resultContent = document.getElementById('resultContent');
    const emptyState = document.getElementById('emptyState');
    const activeViewLabel = document.getElementById('activeViewLabel');
    const resultStats = document.getElementById('resultStats');
    const transcriptionResult = document.getElementById('transcriptionResult');
    const copyBtn = document.getElementById('copyBtn');
    const recalibrateBtn = document.getElementById('recalibrateBtn');
    const summarizeBtn = document.getElementById('summarizeBtn');
    const generateNotesBtn = document.getElementById('generateNotesBtn');
    const sourceTab = document.getElementById('sourceTab');
    const summaryTab = document.getElementById('summaryTab');
    const notesTab = document.getElementById('notesTab');
    const viewTabs = [sourceTab, summaryTab, notesTab];
    const resultsWorkspace = document.querySelector('.workspace');

    const featureConfigured = {
        s2t: document.body.dataset.s2tConfigured === 'true',
        calibration: document.body.dataset.calibrationConfigured === 'true',
        summary: document.body.dataset.summaryConfigured === 'true',
        notes: document.body.dataset.notesConfigured === 'true'
    };

    const viewLabels = {
        source: '原文',
        summary: '摘要',
        notes: '笔记'
    };

    let selectedFile = null;
    let currentRawTranscription = '';
    let currentCalibratedText = '';
    let summaryText = '';
    let notesText = '';
    let activeView = 'source';
    let busy = false;

    function getButtonLabel(button) {
        const label = button.querySelector('[data-button-label]');
        return label ? label.textContent : button.textContent;
    }

    function setButtonLabel(button, text) {
        const label = button.querySelector('[data-button-label]');
        if (label) {
            label.textContent = text;
        } else {
            button.textContent = text;
        }
    }

    function formatFileSize(bytes) {
        if (!Number.isFinite(bytes) || bytes < 0) return '大小未知';
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }

    function getFileType(file) {
        const extension = file.name.includes('.') ? file.name.split('.').pop() : '';
        return extension ? extension.toUpperCase() : '音频';
    }

    function updateStatus(message, type) {
        statusMessage.textContent = message || '';
        statusMessage.className = 'status-message';
        if (!message || !type) {
            statusMessage.hidden = true;
            return;
        }
        statusMessage.classList.add(type);
        statusMessage.hidden = false;
    }

    function updateWorkspaceStatus() {
        if (busy) {
            workspaceStatus.textContent = '正在处理';
            workspaceStatus.dataset.state = 'busy';
            return;
        }
        if (currentCalibratedText.trim()) {
            workspaceStatus.textContent = '处理完成';
            workspaceStatus.dataset.state = 'done';
            return;
        }
        if (selectedFile) {
            workspaceStatus.textContent = '准备就绪';
            workspaceStatus.dataset.state = 'ready';
            return;
        }
        workspaceStatus.textContent = '等待音频';
        workspaceStatus.dataset.state = 'idle';
    }

    function getDisplayedText() {
        if (activeView === 'summary') return summaryText;
        if (activeView === 'notes') return notesText;
        return currentCalibratedText;
    }

    function countCharacters(text) {
        return Array.from(text).length;
    }

    function updateButtons() {
        const hasSource = Boolean(currentCalibratedText.trim());
        const rawWithinLimit = countCharacters(currentRawTranscription) <= MAX_TEXT_CHARS;
        const sourceWithinLimit = countCharacters(currentCalibratedText) <= MAX_TEXT_CHARS;
        submitBtn.disabled = busy || !selectedFile || !featureConfigured.s2t;
        recalibrateBtn.disabled = busy || !featureConfigured.calibration || !currentRawTranscription.trim() || !rawWithinLimit;
        summarizeBtn.disabled = busy || !featureConfigured.summary || !hasSource || !sourceWithinLimit;
        generateNotesBtn.disabled = busy || !featureConfigured.notes || !hasSource || !sourceWithinLimit;
        copyBtn.disabled = busy || !getDisplayedText().trim();
        sourceTab.disabled = !hasSource;
        summaryTab.disabled = !summaryText.trim();
        notesTab.disabled = !notesText.trim();
        updateWorkspaceStatus();
    }

    function renderView(view) {
        if (view === 'summary' && !summaryText.trim()) return;
        if (view === 'notes' && !notesText.trim()) return;
        if (view === 'source' && !currentCalibratedText.trim()) {
            activeView = 'source';
        } else {
            activeView = view;
        }

        const displayedText = getDisplayedText();
        const hasText = Boolean(displayedText.trim());
        transcriptionResult.textContent = displayedText;
        activeViewLabel.textContent = viewLabels[activeView];
        resultStats.textContent = `${countCharacters(displayedText).toLocaleString('zh-CN')} 字符`;
        emptyState.hidden = hasText;
        resultContent.hidden = !hasText;

        viewTabs.forEach(function(tab) {
            const selected = tab.dataset.view === activeView;
            tab.classList.toggle('is-active', selected);
            tab.setAttribute('aria-selected', String(selected));
        });

        setButtonLabel(summarizeBtn, summaryText ? '重新生成摘要' : '生成摘要');
        setButtonLabel(generateNotesBtn, notesText ? '重新生成笔记' : '生成笔记');
        updateButtons();
    }

    function resetDerivedResults() {
        summaryText = '';
        notesText = '';
        activeView = 'source';
        renderView('source');
    }

    function clearResults() {
        currentRawTranscription = '';
        currentCalibratedText = '';
        summaryText = '';
        notesText = '';
        activeView = 'source';
        setButtonLabel(copyBtn, '复制');
        copyBtn.classList.remove('copied-success', 'copied-error');
        renderView('source');
    }

    function setBusy(nextBusy) {
        busy = nextBusy;
        audioFileInput.disabled = nextBusy;
        audioFileLabel.classList.toggle('is-disabled', nextBusy);
        transcriptionForm.setAttribute('aria-busy', String(nextBusy));
        resultsWorkspace.setAttribute('aria-busy', String(nextBusy));
        updateButtons();
    }

    function extractErrorMessage(data, fallback) {
        if (typeof data?.error === 'string') return data.error;
        if (typeof data?.error?.message === 'string') return data.error.message;
        if (typeof data?.message === 'string') return data.message;
        return fallback;
    }

    async function fetchJson(url, options) {
        const response = await fetch(url, options);
        let data;
        try {
            data = await response.json();
        } catch (_error) {
            throw new Error(`服务返回了无效响应 (状态 ${response.status})`);
        }
        if (!response.ok) {
            throw new Error(extractErrorMessage(data, `请求失败 (状态 ${response.status})`));
        }
        return data;
    }

    function handleTranscriptionSuccess(data, operationType) {
        if (typeof data.transcription !== 'string' || !data.transcription.trim()) {
            throw new Error(`${operationType}返回了空文本`);
        }
        if (typeof data.raw_transcription === 'string' && data.raw_transcription.trim()) {
            currentRawTranscription = data.raw_transcription;
        }
        currentCalibratedText = data.transcription;
        resetDerivedResults();
        const overProcessingLimit = countCharacters(currentCalibratedText) > MAX_TEXT_CHARS;
        const messageType = data.is_calibrated ? 'success' : 'info';
        const baseMessage = data.calibration_message || `${operationType}完成。`;
        updateStatus(
            overProcessingLimit
                ? `${baseMessage} 文本超过 ${MAX_TEXT_CHARS.toLocaleString('zh-CN')} 字符，已禁用校准、摘要和笔记处理。`
                : baseMessage,
            overProcessingLimit ? 'info' : messageType
        );
    }

    function handleSelectedFile(file) {
        clearResults();
        updateStatus('', null);

        if (!file) {
            selectedFile = null;
            fileSummary.hidden = true;
            audioFileLabel.classList.remove('has-file');
            updateButtons();
            return;
        }
        if (file.size === 0) {
            selectedFile = null;
            audioFileInput.value = '';
            fileSummary.hidden = true;
            audioFileLabel.classList.remove('has-file');
            updateStatus('音频文件不能为空。', 'error');
            updateButtons();
            return;
        }
        if (file.size > MAX_UPLOAD_BYTES) {
            selectedFile = null;
            audioFileInput.value = '';
            fileSummary.hidden = true;
            audioFileLabel.classList.remove('has-file');
            updateStatus(`音频文件过大，最大支持 ${MAX_UPLOAD_MB} MB。`, 'error');
            updateButtons();
            return;
        }

        selectedFile = file;
        fileNameDisplay.textContent = file.name;
        fileMetaDisplay.textContent = `${getFileType(file)} · ${formatFileSize(file.size)}`;
        fileSummary.hidden = false;
        audioFileLabel.classList.add('has-file');
        updateButtons();
    }

    audioFileInput.addEventListener('change', function(event) {
        handleSelectedFile(event.target.files[0]);
    });

    ['dragenter', 'dragover'].forEach(function(eventName) {
        audioFileLabel.addEventListener(eventName, function(event) {
            event.preventDefault();
            if (!busy) audioFileLabel.classList.add('is-dragging');
        });
    });

    ['dragleave', 'drop'].forEach(function(eventName) {
        audioFileLabel.addEventListener(eventName, function(event) {
            event.preventDefault();
            audioFileLabel.classList.remove('is-dragging');
        });
    });

    audioFileLabel.addEventListener('drop', function(event) {
        if (busy) return;
        const file = event.dataTransfer?.files?.[0];
        if (!file) return;
        try {
            const transfer = new DataTransfer();
            transfer.items.add(file);
            audioFileInput.files = transfer.files;
        } catch (_error) {
            audioFileInput.value = '';
        }
        handleSelectedFile(file);
    });

    viewTabs.forEach(function(tab) {
        tab.addEventListener('click', function() {
            renderView(tab.dataset.view);
        });
    });

    transcriptionForm.addEventListener('submit', async function(event) {
        event.preventDefault();
        if (!selectedFile) {
            updateStatus('未选择音频文件。', 'error');
            return;
        }
        if (!featureConfigured.s2t) {
            updateStatus('语音转录服务尚未配置。', 'error');
            return;
        }

        const formData = new FormData();
        formData.append('audio_file', selectedFile);
        const originalText = getButtonLabel(submitBtn);
        updateStatus('正在上传并转录音频...', 'info');
        setBusy(true);
        setButtonLabel(submitBtn, '转录中');

        try {
            const data = await fetchJson('/api/transcribe', { method: 'POST', body: formData });
            if (data.status !== 'success') {
                throw new Error(extractErrorMessage(data, '转录结果格式无效。'));
            }
            handleTranscriptionSuccess(data, '转录');
        } catch (error) {
            console.error('转录错误:', error);
            clearResults();
            updateStatus(`转录失败: ${error.message}`, 'error');
        } finally {
            setButtonLabel(submitBtn, originalText);
            setBusy(false);
        }
    });

    recalibrateBtn.addEventListener('click', async function() {
        if (!currentRawTranscription.trim()) {
            updateStatus('没有可校准的原始转录。', 'info');
            return;
        }

        const originalText = getButtonLabel(recalibrateBtn);
        updateStatus('正在重新校准文本...', 'info');
        setBusy(true);
        setButtonLabel(recalibrateBtn, '校准中');
        try {
            const data = await fetchJson('/api/recalibrate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ raw_transcription: currentRawTranscription })
            });
            if (data.status !== 'success') {
                throw new Error(extractErrorMessage(data, '重新校准失败。'));
            }
            handleTranscriptionSuccess(data, '重新校准');
        } catch (error) {
            console.error('重新校准错误:', error);
            updateStatus(`重新校准失败: ${error.message}`, 'error');
        } finally {
            setButtonLabel(recalibrateBtn, originalText);
            setBusy(false);
        }
    });

    summarizeBtn.addEventListener('click', async function() {
        if (!currentCalibratedText.trim()) {
            updateStatus('没有可生成摘要的文本。', 'info');
            return;
        }

        const originalText = getButtonLabel(summarizeBtn);
        updateStatus('正在生成摘要...', 'info');
        setBusy(true);
        setButtonLabel(summarizeBtn, '生成中');
        try {
            const data = await fetchJson('/api/summarize', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text_to_summarize: currentCalibratedText })
            });
            if (typeof data.summary !== 'string' || !data.summary.trim()) {
                throw new Error('服务未返回有效摘要。');
            }
            summaryText = data.summary;
            renderView('summary');
            updateStatus('摘要已生成。', 'success');
        } catch (error) {
            console.error('生成摘要错误:', error);
            updateStatus(`摘要生成失败: ${error.message}`, 'error');
            renderView('source');
        } finally {
            setButtonLabel(summarizeBtn, summaryText ? '重新生成摘要' : originalText);
            setBusy(false);
        }
    });

    generateNotesBtn.addEventListener('click', async function() {
        if (!currentCalibratedText.trim()) {
            updateStatus('没有可生成笔记的文本。', 'info');
            return;
        }

        const originalText = getButtonLabel(generateNotesBtn);
        updateStatus('正在生成结构化笔记...', 'info');
        setBusy(true);
        setButtonLabel(generateNotesBtn, '生成中');
        try {
            const data = await fetchJson('/api/generatenote', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text_to_process: currentCalibratedText })
            });
            if (typeof data.notes !== 'string' || !data.notes.trim()) {
                throw new Error('服务未返回有效笔记。');
            }
            notesText = data.notes;
            renderView('notes');
            updateStatus('笔记已生成。', 'success');
        } catch (error) {
            console.error('生成笔记错误:', error);
            updateStatus(`笔记生成失败: ${error.message}`, 'error');
            renderView('source');
        } finally {
            setButtonLabel(generateNotesBtn, notesText ? '重新生成笔记' : originalText);
            setBusy(false);
        }
    });

    async function writeToClipboard(text) {
        if (navigator.clipboard && window.isSecureContext) {
            try {
                await navigator.clipboard.writeText(text);
                return;
            } catch (_error) {
                // Permission can be denied even in secure contexts; fall back
                // to the legacy textarea strategy instead of failing outright.
            }
        }
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.setAttribute('readonly', '');
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        let copied = false;
        try {
            copied = document.execCommand('copy');
        } catch (_error) {
            copied = false;
        }
        document.body.removeChild(textarea);
        if (!copied) throw new Error('浏览器不支持复制操作');
    }

    copyBtn.addEventListener('click', async function() {
        const textToCopy = getDisplayedText();
        if (!textToCopy.trim()) {
            updateStatus('当前视图没有可复制的内容。', 'info');
            return;
        }

        copyBtn.classList.remove('copied-success', 'copied-error');
        try {
            await writeToClipboard(textToCopy);
            setButtonLabel(copyBtn, '已复制');
            copyBtn.classList.add('copied-success');
        } catch (error) {
            console.error('无法复制文本:', error);
            setButtonLabel(copyBtn, '复制失败');
            copyBtn.classList.add('copied-error');
        }
        window.setTimeout(function() {
            setButtonLabel(copyBtn, '复制');
            copyBtn.classList.remove('copied-success', 'copied-error');
        }, 2000);
    });

    if (!featureConfigured.s2t) {
        submitBtn.title = '语音转录服务尚未配置';
    }
    if (!featureConfigured.calibration) {
        recalibrateBtn.title = '校准服务尚未配置';
    }
    if (!featureConfigured.summary) {
        summarizeBtn.title = '摘要服务尚未配置';
    }
    if (!featureConfigured.notes) {
        generateNotesBtn.title = '笔记服务尚未配置';
    }

    clearResults();
    updateButtons();
});
