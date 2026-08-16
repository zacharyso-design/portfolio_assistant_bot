export interface DropIntake {
  files: File[];
  warning: string;
}

// Dragging a OneDrive online-only placeholder out of Explorer hands the
// browser a drop event whose file list is empty with no other payload.
export const EMPTY_DROP_WARNING =
  "Windows handed the browser an empty drop. If the file lives in OneDrive it may be "
  + "online-only: right-click it in Explorer and choose \"Always keep on this device\", "
  + "or use the Choose file button instead.";

// Dragging an Outlook email, a text selection, or an image from another
// window also yields zero files, but carries non-file payload types - a
// different problem needing different advice.
export const NON_FILE_DROP_WARNING =
  "That drop did not contain a file the browser can read. Save an Outlook email "
  + "as a .msg file first (or drag it from Explorer), then drop the file here or "
  + "use the Choose file button.";

export function intakeFromDrop(
  dropped: ArrayLike<File>, hasNonFilePayload = false,
): DropIntake {
  const files = Array.from(dropped);
  if (files.length) return { files, warning: "" };
  return { files, warning: hasNonFilePayload ? NON_FILE_DROP_WARNING : EMPTY_DROP_WARNING };
}
