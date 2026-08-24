/* ============================================================
   AI 创意工坊 - Service Worker
   ------------------------------------------------------------
   作用：
     1. 预缓存应用外壳（index.html / manifest / 图标），
        使应用可离线打开，秒开体验接近原生 App；
     2. 网络优先策略：有网时始终获取最新页面，
        离线时回退到缓存副本，保证基本可用。

   注意：本文件不缓存任何图片生成结果（动态数据），
   只缓存静态应用外壳，避免磁盘膨胀。
   ============================================================ */

// 缓存名称：带版本号，升级发布时更新版本号即可自动清理旧缓存
const CACHE_NAME = "ai-creative-workshop-v1";

// 需要预缓存的应用外壳资源（相对路径，与部署目录结构一致）
const APP_SHELL = [
  "./",
  "./index.html",
  "./manifest.json",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
];

/* ------------------------------------------------------------
   install 阶段：预缓存应用外壳
   ------------------------------------------------------------ */
self.addEventListener("install", (event) => {
  // waitUntil 确保缓存写入完成前，Service Worker 不会进入激活阶段
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(APP_SHELL))
      // 安装成功后立即接管页面，避免等待旧版本释放
      .then(() => self.skipWaiting())
  );
});

/* ------------------------------------------------------------
   activate 阶段：清理旧版本缓存
   ------------------------------------------------------------ */
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        // 删除不属于当前版本的缓存，防止磁盘空间被旧资源占用
        Promise.all(
          keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
        )
      )
      // 立即控制所有已打开的页面，无需用户刷新
      .then(() => self.clients.claim())
  );
});

/* ------------------------------------------------------------
   fetch 阶段：网络优先，离线回退
   ------------------------------------------------------------
   策略说明：
   - 页面与静态资源：优先走网络（保证内容最新），
     失败时回退缓存（保证离线可用）；
   - API 请求（/api/ 开头）：绝不缓存，全部走网络。
   ------------------------------------------------------------ */
self.addEventListener("fetch", (event) => {
  const requestUrl = new URL(event.request.url);

  // 1. API 请求：直接放行，走网络（包含生成的图片 URL 也属于动态内容）
  if (requestUrl.pathname.startsWith("/api/")) {
    return;
  }

  // 2. 非 GET 请求（如 POST 生成请求）：直接放行
  if (event.request.method !== "GET") {
    return;
  }

  // 3. 静态资源：网络优先 + 缓存回退
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        // 网络成功：更新缓存副本（仅缓存同源且有效的响应）
        if (response && response.status === 200 && response.type === "basic") {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return response;
      })
      .catch(() =>
        // 网络失败（离线）：回退到缓存
        caches.match(event.request).then((cached) => cached || caches.match("./"))
      )
  );
});
