// Lumen 是本地单用户桌面应用，user_id 固定。
// 历史上用 localStorage 随机 UUID 导致清缓存就丢数据归属（2026-05-17 故障）。
export function getUserId(): string {
  return "demo_user";
}
