// IndexedDB can store the optional team PNGs as well as unfinished rosters with blank lobby names.
let connection;
let revision = 0;
let pending = Promise.resolve();

function database() {
  if (connection) return connection;
  connection = (async () => {
    const options = await window.__TAURI__?.core?.invoke("launch_options");
    const name = options?.selftest ? "MSSB Game Plan QA " + options.dir : "MSSB Game Plan";
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(name, 1);
      request.onupgradeneeded = () => request.result.createObjectStore("drafts");
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  })().catch(error => { connection = undefined; throw error; });
  return connection;
}

export async function loadDraft() {
  await pending;
  const db = await database();
  return new Promise((resolve, reject) => {
    const request = db.transaction("drafts").objectStore("drafts").get("current");
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      revision = request.result?.revision ?? 0;
      resolve(request.result?.snapshot ?? null);
    };
  });
}

export function saveDraft(snapshot) {
  const write = pending.then(async () => {
    const db = await database();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction("drafts", "readwrite");
      const store = transaction.objectStore("drafts");
      const previous = store.get("current");
      let conflict = false;
      previous.onsuccess = () => {
        if ((previous.result?.revision ?? 0) !== revision) {
          conflict = true;
          transaction.abort();
          return;
        }
        store.put({revision: revision + 1, snapshot}, "current");
      };
      transaction.oncomplete = () => { revision++; resolve(); };
      transaction.onabort = () => reject(conflict
        ? new Error("Another cloner window saved a newer plan. Export this team before reopening the cloner.")
        : transaction.error || new Error("Could not save the roster draft on this device."));
    });
  });
  pending = write.catch(() => {});
  return write;
}
