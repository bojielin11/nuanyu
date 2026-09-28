App({
  globalData: {
    // Port 5004 is an ADB loopback socket. The EXE exposes the mini-program
    // gateway on the PC's LAN address and port 5005.
    //
    // 注意：电脑的 IP 是 DHCP 动态分配的，换一次网络就变一次。
    // 这里刻意不内置任何局域网地址：apiBaseUrl 留空即可，小程序会自动按
    // discovery 里的子网扫描 5005 网关（命中即缓存），实现零配置可用；
    // 如需指定首选地址，可手动填 http://电脑IP:5005。
    apiBaseUrl: "",
    apiFallbackUrls: ["http://127.0.0.1:5005"],
    // 局域网自动发现（抄底）配置：
    //   port      —— 暖语 EXE 小程序网关端口
    //   subnets   —— 依次扫描的子网（靠前的先扫，常见热点排前面）
    //   hostStart/hostEnd —— 每子网扫描的主机号区间
    // iPhone 热点默认 172.20.10.x（多为 /28，主机只到 ~15）；旧局域网 192.168.5.x。
    // 范围刻意收窄（.2-.25）避免一次发太多请求被微信限流/拖慢。
    discovery: {
      port: 5005,
      subnets: ["172.20.10", "192.168.5"],
      hostStart: 2,
      hostEnd: 25
    }
  },

  onLaunch() {
    // 后台预热：一进入小程序就尝试定位网关并缓存，页面加载即命中。
    try {
      const api = require('./services/api')
      if (api && typeof api.prewarm === 'function') {
        api.prewarm()
      }
    } catch (e) {
      // 预热失败不影响页面正常请求流程
    }
  }
})
