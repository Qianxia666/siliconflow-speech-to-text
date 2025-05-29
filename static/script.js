document.addEventListener('DOMContentLoaded', () => {
    const transcriptionForm = document.getElementById('transcriptionForm');
    const submitBtn = document.getElementById('submitBtn');
    const audioFileIn = document.getElementById('audioFile');
    const apiEndpointIn = document.getElementById('apiEndpoint');
    const apiKeyIn = document.getElementById('apiKey');
    const modelNameIn = document.getElementById('modelName');
    const transcriptionResultEl = document.getElementById('transcriptionResult');
    const statusMessageEl = document.getElementById('statusMessage');
    const copyBtn = document.getElementById('copyBtn');
const downloadMdBtn = document.getElementById('downloadMdBtn');

    // 添加复制按钮点击事件
    copyBtn.addEventListener('click', async () => {
        const textToCopy = transcriptionResultEl.textContent;
        try {
            await navigator.clipboard.writeText(textToCopy);
            const originalText = copyBtn.textContent;
            copyBtn.textContent = '已复制！';
            copyBtn.classList.add('bg-green-500');
            setTimeout(() => {
                copyBtn.textContent = originalText;
                copyBtn.classList.remove('bg-green-500');
            }, 2000);
        } catch (err) {
            console.error('复制失败:', err);
            copyBtn.textContent = '复制失败';
            copyBtn.classList.add('bg-red-500');
            setTimeout(() => {
                copyBtn.textContent = '复制文本';
                copyBtn.classList.remove('bg-red-500');
            }, 2000);
        }
    });

ddownloadMdBtn.addEventListener('click', () => {    const textToDownload = transcriptionResultEl.textContent;    if (!textToDownload) {        console.warn('No text to download.');        return;    }    const blob = new Blob([textToDownload], { type: 'text/markdown;charset=utf-8' });    const url = URL.createObjectURL(blob);    const a = document.createElement('a');    a.href = url;    a.download = 'transcription.md';    document.body.appendChild(a);    a.click();    document.body.removeChild(a);    URL.revokeObjectURL(url);    const originalText = downloadMdBtn.textContent;    downloadMdBtn.textContent = 'Downloaded!';    setTimeout(() => {        downloadMdBtn.textContent = originalText;    }, 2000);});
    // Function to save form data to localStorage
    const saveFormData = () => {
        localStorage.setItem('apiEndpoint', apiEndpointIn.value);
        localStorage.setItem('apiKey', apiKeyIn.value);
        localStorage.setItem('modelName', modelNameIn.value);
    };

    // Function to load form data from localStorage
    const loadFormData = () => {
        apiEndpointIn.value = localStorage.getItem('apiEndpoint') || '';
        apiKeyIn.value = localStorage.getItem('apiKey') || '';
        modelNameIn.value = localStorage.getItem('modelName') || '';
    };

    // Load form data when the page loads
    loadFormData();

    // Save form data whenever an input changes
    apiEndpointIn.addEventListener('input', saveFormData);
    apiKeyIn.addEventListener('input', saveFormData);
    modelNameIn.addEventListener('input', saveFormData);

    submitBtn.addEventListener('click', async (event) => {
        event.preventDefault();

        statusMessageEl.textContent = '';
        statusMessageEl.className = 'status-message';
        transcriptionResultEl.textContent = '';
        copyBtn.classList.add('hidden'); // 隐藏复制按钮
downloadMdBtn.classList.add('hidden'); // 隐藏下载按钮

        const audioFile = audioFileIn.files[0];
        const apiEndpointUrl = apiEndpointIn.value.trim();
        const apiKey = apiKeyIn.value.trim();
        const modelName = modelNameIn.value.trim();

        if (!audioFile) {
            statusMessageEl.textContent = '请选择一个音频文件。';
            statusMessageEl.classList.add('error');
            return;
        }
        if (!apiEndpointUrl) {
            statusMessageEl.textContent = '请输入 API 端点 URL。';
            statusMessageEl.classList.add('error');
            return;
        }
        if (!apiKey) {
            statusMessageEl.textContent = '请输入 API 密钥。';
            statusMessageEl.classList.add('error');
            return;
        }
        if (!modelName) {
            statusMessageEl.textContent = '请输入模型名称。';
            statusMessageEl.classList.add('error');
            return;
        }

        const formData = new FormData();
        formData.append('audio_file', audioFile);
        formData.append('api_endpoint_url', apiEndpointUrl);
        formData.append('api_key', apiKey);
        formData.append('model_name', modelName);

        statusMessageEl.textContent = '正在处理中，请稍候...';
        statusMessageEl.classList.add('info');
        submitBtn.disabled = true;
        submitBtn.textContent = '处理中...';

        try {
            const response = await fetch('/transcribe', {
                method: 'POST',
                body: formData
            });

            const result = await response.json();

            if (response.ok && result.status === 'success') {
                transcriptionResultEl.textContent = result.transcription;
                statusMessageEl.textContent = '转录成功！';
                statusMessageEl.className = 'status-message success';
                copyBtn.classList.remove('hidden'); // 显示复制按钮
downloadMdBtn.classList.remove('hidden'); // 显示下载按钮
            } else {
                transcriptionResultEl.textContent = '';
                statusMessageEl.textContent = `错误: ${result.message || '未知错误'}`;
downloadMdBtn.classList.add('hidden');
                statusMessageEl.className = 'status-message error';
            }

        } catch (error) {
            console.error('Fetch error:', error);
            transcriptionResultEl.textContent = '';
            statusMessageEl.textContent = '请求失败，请检查网络连接或联系管理员。';
            statusMessageEl.className = 'status-message error';
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = '开始转录';
        }
    });
}); 