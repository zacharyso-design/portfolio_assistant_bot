export interface DropIntake {
  files: File[];
  warning: string;
}

// Dragging a OneDrive online-only placeholder out of Explorer hands the
// browser a drop event whose file list is empty. Both drop zones used to
// ignore that silently, which reads as "drag and drop does not work".
export const EMPTY_DROP_WARNING =
  "Windows handed the browser an empty drop. If the file lives in OneDrive it may be "
  + "online-only: right-click it in Explorer and choose \"Always keep on this device\", "
  + "or use the Choose file button instead.";

export function intakeFromDrop(dropped: ArrayLike<File>): DropIntake {
  const files = Array.from(dropped);
  return { files, warning: files.length ? "" : EMPTY_DROP_WARNING };
}
