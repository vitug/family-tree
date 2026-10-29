/* Service Worker: семейное дерево.
Минимальный pass-through — нужен для installability (Chrome/Edge/Android).
Статику и API не кэшируем: данные живые, авторизация по cookie. */
self.addEventListener("install", (e) => {
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  /* Просто пропускаем запросы к сети; офлайн-кэш не делаем:
     дерево без API бесполезно, а кэшировать защищённые данные нельзя. */
  return;
});
