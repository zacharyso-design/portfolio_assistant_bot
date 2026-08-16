import { useId, useState } from "react";

import { intakeFromDrop } from "../lib/dropIntake";

export function IngestionDropZone({ onFiles }: { onFiles: (files: File[]) => void }) {
  const filesId = useId();
  const folderId = useId();
  const [dropWarning, setDropWarning] = useState("");
  return <section className="dropzone ingestion-dropzone" onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); const { files, warning } = intakeFromDrop(event.dataTransfer.files); setDropWarning(warning); if (files.length) onFiles(files); }}><span>+</span><strong>Drop files, email, documents, or transcripts</strong><small>Use Choose folder to preserve a folder tree. One selection becomes one durable ingestion package.</small>{dropWarning && <small className="notice error">{dropWarning}</small>}<div className="header-actions"><label className="button" htmlFor={filesId}>Choose files</label><label className="button" htmlFor={folderId}>Choose folder</label></div><input id={filesId} type="file" multiple onChange={event => { const files = [...(event.target.files || [])]; if (files.length) { setDropWarning(""); onFiles(files); } event.target.value = ""; }} /><input id={folderId} type="file" multiple ref={element => { if (element) element.setAttribute("webkitdirectory", ""); }} onChange={event => { const files = [...(event.target.files || [])]; if (files.length) { setDropWarning(""); onFiles(files); } event.target.value = ""; }} /></section>;
}

export function DropZone({ label, onFile, description = "MSG, EML, TXT, MD, VTT, SRT, DOCX, or text-layer PDF", accept }: { label: string; onFile: (file: File) => void; description?: string; accept?: string }) {
  const id = useId();
  const [dropWarning, setDropWarning] = useState("");
  return <label className="dropzone" htmlFor={id} onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); const { files, warning } = intakeFromDrop(event.dataTransfer.files); setDropWarning(warning); if (files[0]) onFile(files[0]); }}><span>⇧</span><strong>{label}</strong><small>{description}</small>{dropWarning && <small className="notice error">{dropWarning}</small>}<span className="button">Choose file</span><input id={id} type="file" accept={accept} onChange={event => { const file = event.target.files?.[0]; if (file) { setDropWarning(""); onFile(file); } event.target.value = ""; }} /></label>;
}
