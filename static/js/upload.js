/**
 * upload.js — Direct-to-R2 presigned upload UI component
 * Reusable upload widget: drag-drop, preview, progress bar, validation
 */
'use strict';

class R2Uploader {
  /**
   * @param {Object} opts
   * @param {string}   opts.triggerSelector  — Button/area that opens file picker
   * @param {string}   opts.previewSelector  — <img> or <video> element for preview
   * @param {string}   opts.progressSelector — Progress bar fill element
   * @param {string}   opts.category         — R2 folder: 'posts'|'reels'|'stories'|'avatars'
   * @param {string[]} opts.accept           — MIME types e.g. ['image/jpeg','video/mp4']
   * @param {Function} opts.onComplete       — cb(objectKey, previewUrl)
   * @param {Function} opts.onError          — cb(errorMessage)
   */
  constructor(opts = {}) {
    this.category = opts.category || 'posts';
    this.accept   = opts.accept || ['image/jpeg','image/png','image/webp','video/mp4'];
    this.onComplete = opts.onComplete || (() => {});
    this.onError    = opts.onError    || (msg => Toast.error(msg));

    this._trigger  = opts.triggerSelector  ? document.querySelector(opts.triggerSelector)  : null;
    this._preview  = opts.previewSelector  ? document.querySelector(opts.previewSelector)  : null;
    this._progress = opts.progressSelector ? document.querySelector(opts.progressSelector) : null;

    this._file      = null;
    this._objectKey = null;

    this._input = document.createElement('input');
    this._input.type   = 'file';
    this._input.accept = this.accept.join(',');
    this._input.style.display = 'none';
    document.body.appendChild(this._input);

    this._bindEvents();
  }

  _bindEvents() {
    this._trigger?.addEventListener('click', () => this._input.click());
    this._input.addEventListener('change', e => {
      const f = e.target.files[0];
      if (f) this._handleFile(f);
    });

    // Drag-drop on trigger element
    if (this._trigger) {
      this._trigger.addEventListener('dragover', e => {
        e.preventDefault();
        this._trigger.classList.add('drag-over');
      });
      this._trigger.addEventListener('dragleave', () => {
        this._trigger.classList.remove('drag-over');
      });
      this._trigger.addEventListener('drop', e => {
        e.preventDefault();
        this._trigger.classList.remove('drag-over');
        const f = e.dataTransfer.files[0];
        if (f) this._handleFile(f);
      });
    }
  }

  _handleFile(file) {
    // Validate MIME type
    if (!this.accept.includes(file.type)) {
      this.onError(`File type "${file.type}" is not allowed.`);
      return;
    }
    // Validate size (500 MB max for direct uploads)
    if (file.size > 500 * 1024 * 1024) {
      this.onError('File is too large. Maximum size is 500 MB.');
      return;
    }

    this._file = file;
    this._showPreview(file);
  }

  _showPreview(file) {
    if (!this._preview) return;
    const url = URL.createObjectURL(file);

    if (file.type.startsWith('image/')) {
      if (this._preview.tagName === 'IMG') {
        this._preview.src = url;
      } else {
        this._preview.style.backgroundImage = `url(${url})`;
      }
    } else if (file.type.startsWith('video/')) {
      if (this._preview.tagName === 'VIDEO') {
        this._preview.src = url;
        this._preview.load();
      }
    }
  }

  async upload() {
    if (!this._file) {
      this.onError('No file selected.');
      return null;
    }

    this._setProgress(0);

    try {
      const objectKey = await uploadToR2(
        this._file,
        this.category,
        pct => this._setProgress(pct)
      );
      this._objectKey = objectKey;
      this._setProgress(1);
      this.onComplete(objectKey, URL.createObjectURL(this._file));
      return objectKey;
    } catch (err) {
      this._setProgress(0);
      this.onError(err.message || 'Upload failed.');
      return null;
    }
  }

  _setProgress(fraction) {
    if (!this._progress) return;
    const pct = Math.round(fraction * 100);
    this._progress.style.width = `${pct}%`;
    this._progress.setAttribute('aria-valuenow', pct);
    this._progress.closest('[role="progressbar"]')
      ?.setAttribute('aria-valuenow', pct);
  }

  reset() {
    this._file      = null;
    this._objectKey = null;
    this._input.value = '';
    if (this._preview?.tagName === 'IMG') this._preview.src = '';
    if (this._preview?.tagName === 'VIDEO') this._preview.src = '';
    this._setProgress(0);
  }

  get objectKey() { return this._objectKey; }
  get file()      { return this._file; }
}

/* ── Post Creator ─────────────────────────────────────────────────────────── */
function initPostCreator() {
  const modal   = document.getElementById('postCreatorModal');
  const openBtn = document.getElementById('openPostCreator');
  const closeBtn = modal?.querySelector('.pc-close');
  const submitBtn = document.getElementById('pcSubmit');
  const captionEl = document.getElementById('pcCaption');
  const progressFill = document.getElementById('pcProgressFill');
  const mediaZone = document.getElementById('pcMediaZone');
  const previewEl = document.getElementById('pcPreview');

  if (!modal || !openBtn) return;

  const uploader = new R2Uploader({
    triggerSelector:  '#pcMediaZone',
    previewSelector:  '#pcPreview',
    progressSelector: '#pcProgressFill',
    category: 'posts',
    accept: ['image/jpeg','image/png','image/webp','image/gif','video/mp4','video/webm'],
    onComplete: (key) => {
      mediaZone.style.display = 'none';
      document.getElementById('pcPreviewWrap').style.display = 'block';
      Toast.success('Media ready!');
    },
    onError: msg => Toast.error(msg),
  });

  openBtn.addEventListener('click', () => { modal.hidden = false; });
  closeBtn?.addEventListener('click', () => { modal.hidden = true; uploader.reset(); });
  modal.addEventListener('click', e => { if (e.target === modal) { modal.hidden = true; uploader.reset(); } });

  submitBtn?.addEventListener('click', async () => {
    const caption = captionEl?.value.trim();
    if (!uploader.file && !caption) {
      Toast.error('Add a photo/video or write a caption.');
      return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = 'Posting…';

    try {
      let objectKey = null;
      let mediaType = 'text';

      if (uploader.file) {
        objectKey = await uploader.upload();
        if (!objectKey) { submitBtn.disabled = false; submitBtn.textContent = 'Share'; return; }
        mediaType = uploader.file.type.startsWith('video') ? 'video' : 'image';
      }

      await API.post('/feed/posts', {
        caption,
        media_url:  objectKey ? `/r2/${objectKey}` : null,
        media_type: mediaType,
      });

      Toast.success('Post shared!');
      modal.hidden = true;
      uploader.reset();
      captionEl.value = '';
      // Reload feed
      window.location.reload();
    } catch (err) {
      Toast.error(err.message || 'Could not share post.');
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Share';
    }
  });
}

document.addEventListener('DOMContentLoaded', initPostCreator);
