// Create Post page JavaScript
// API helper (same as in app.js)
const api = {
  async _fetch(method, path, body) {
    const opts = {
      method,
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    if (res.status === 401) {
      window.location.href = "/auth/login";
      return new Promise(() => {});
    }
    const json = await res.json().catch(() => ({ ok: false, error: "Server error" }));
    if (!json.ok) {
      const err = new Error(json.error || "Request failed");
      err.status = res.status;
      throw err;
    }
    return json;
  },
  get:    (p)    => api._fetch("GET",    p),
  post:   (p, b) => api._fetch("POST",   p, b),
  delete: (p)    => api._fetch("DELETE", p),
};

// Toast helper
const toast = (msg, ms = 3000) => {
  const el = document.getElementById("toast");
  if (el) {
    el.textContent = msg;
    el.classList.add("show");
    setTimeout(() => el.classList.remove("show"), ms);
  }
};

document.addEventListener('DOMContentLoaded', function() {
  const dropZone = document.getElementById('post-drop-zone');
  const fileInput = document.getElementById('post-file-input');
  const previewEl = document.getElementById('post-preview');
  const previewImg = document.getElementById('post-preview-img');
  const previewVid = document.getElementById('post-preview-vid');
  const postForm = document.getElementById('post-form');
  const progressWrap = document.getElementById('upload-progress');
  const btnSubmit = document.getElementById('btn-submit-post');
  
  let selectedFile = null;

  // Handle file selection
  const handleFile = (file) => {
    selectedFile = file;
    dropZone.hidden = true;
    previewEl.hidden = false;
    postForm.hidden = false;
    btnSubmit.disabled = false;
    
    const isVid = file.type.startsWith('video/');
    previewImg.hidden = isVid;
    previewVid.hidden = !isVid;
    
    const url = URL.createObjectURL(file);
    if (isVid) {
      previewVid.src = url;
      document.getElementById('post-type').value = 'video';
    } else {
      previewImg.src = url;
    }
  };
  
  // Reset form
  const resetForm = () => {
    selectedFile = null;
    dropZone.hidden = false;
    previewEl.hidden = true;
    postForm.hidden = true;
    progressWrap.hidden = true;
    btnSubmit.disabled = true;
    previewImg.src = '';
    previewVid.src = '';
    fileInput.value = '';
    document.getElementById('post-caption').value = '';
    document.getElementById('post-location').value = '';
  };

  // Event listeners
  dropZone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    if (fileInput.files[0]) handleFile(fileInput.files[0]);
  });
  
  dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('dragover');
  });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
  });

  document.getElementById('btn-browse-files').addEventListener('click', () => fileInput.click());
  document.getElementById('btn-change-media').addEventListener('click', () => {
    previewEl.hidden = true;
    postForm.hidden = true;
    dropZone.hidden = false;
    fileInput.value = '';
    selectedFile = null;
  });

  // Submit post
  btnSubmit.addEventListener('click', async () => {
    if (!selectedFile) {
      toast('Please select a file first.');
      return;
    }
    
    btnSubmit.disabled = true;
    previewEl.hidden = true;
    postForm.hidden = true;
    progressWrap.hidden = false;

    try {
      // Get presigned URL
      const presign = await api.post('/api/upload/presign', {
        content_type: selectedFile.type,
        filename: selectedFile.name,
      });

      // Upload to R2
      await uploadToR2(presign.data.upload_url, selectedFile, (pct) => {
        const ring = document.getElementById('progress-ring-fill');
        if (ring) ring.style.strokeDashoffset = 113 - (113 * pct / 100);
        const pctEl = document.getElementById('upload-pct');
        if (pctEl) pctEl.textContent = pct + '%';
      });

      // Create post
      await api.post('/api/posts', {
        media_key: presign.data.object_key,
        media_type: document.getElementById('post-type').value,
        caption: document.getElementById('post-caption').value.trim(),
        location: document.getElementById('post-location').value.trim(),
      });

      toast('Posted successfully!');
      window.location.href = '/';
    } catch (err) {
      progressWrap.hidden = true;
      previewEl.hidden = false;
      postForm.hidden = false;
      toast('Upload failed: ' + err.message);
    } finally {
      btnSubmit.disabled = false;
    }
  });
});

// Helper function for R2 upload
async function uploadToR2(url, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', url);
    xhr.setRequestHeader('Content-Type', file.type);
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
    });
    xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new Error('Upload error ' + xhr.status)));
    xhr.onerror = () => reject(new Error('Network error during upload'));
    xhr.send(file);
  });
}
