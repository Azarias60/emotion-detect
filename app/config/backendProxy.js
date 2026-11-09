// 仅处理开发代理无法连接上游的错误；后端业务响应直接透传。
export function configureBackendProxy(proxy) {
  proxy.on('error', (_error, _request, response) => {
    if (typeof response.writeHead !== 'function' || response.headersSent || response.writableEnded)
      return
    response.writeHead(503, { 'Content-Type': 'application/json; charset=utf-8' })
    response.end(JSON.stringify({
      code: 503,
      msg: '暂时无法连接后端服务（8081）。若刚启动，请等待模型加载完成后重试；否则请检查后端是否运行。',
    }))
  })
}
