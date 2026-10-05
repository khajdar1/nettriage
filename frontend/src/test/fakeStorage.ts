/**
 * A stand-in for S3 in tests: answers the browser's PUT of a file (an `XMLHttpRequest`, for its
 * progress events) and keeps what was sent. Vitest restores the real one after each test.
 */
import { vi } from "vitest";

export interface StoredPut {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: ArrayBuffer;
}

/** `status: 0` stands for a network failure. */
export function fakeStorage({ status = 200 }: { status?: number } = {}): { puts: StoredPut[] } {
  const puts: StoredPut[] = [];
  class FakeRequest {
    upload: { onprogress: ((event: ProgressEvent) => void) | null } = { onprogress: null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    status = 0;
    private method = "";
    private url = "";
    private headers: Record<string, string> = {};

    open(method: string, url: string) {
      this.method = method;
      this.url = url;
    }

    setRequestHeader(name: string, value: string) {
      this.headers[name] = value;
    }

    send(body: ArrayBuffer) {
      puts.push({ method: this.method, url: this.url, headers: { ...this.headers }, body });
      queueMicrotask(() => {
        const total = body.byteLength;
        this.upload.onprogress?.({
          lengthComputable: true,
          loaded: total / 2,
          total,
        } as ProgressEvent);
        this.status = status;
        if (status === 0) {
          this.onerror?.();
        } else {
          this.onload?.();
        }
      });
    }
  }
  vi.stubGlobal("XMLHttpRequest", FakeRequest);
  return { puts };
}
