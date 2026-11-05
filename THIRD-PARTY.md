# Third-party components

Python dependencies: PyYAML (MIT); optional faster-whisper (MIT) and the Whisper `small` model weights you download yourself (MIT, OpenAI). Dev only: ruff (MIT), pytest (MIT), pytest-cov (MIT).

External programs called as separate processes, not bundled: `pdftotext` from poppler (GPL-2.0/3.0) and `rg` from ripgrep (MIT or Unlicense).

`sourceshelf describe` can send source text to a third-party model endpoint you configure; see the privacy warnings in `README.md`. Source texts you fetch stay subject to their own copyright and licences; check them before you store or share a library. If a library holds personal data, data-protection law applies to it (for example the GDPR); `confidential: yes` is a local safeguard, not a compliance measure.
