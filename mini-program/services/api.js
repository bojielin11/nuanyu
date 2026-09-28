const app = getApp()

const STORAGE_KEY = 'nuanyu_gateway'

// 会话内已确认的网关；首次成功后不再重复探测，后续请求秒级直达。
let _resolved = ''

function getGlobal() {
  try { return app.globalData || {} } catch (e) { return {} }
}

function candidateBases() {
  const g = getGlobal()
  const configured = g.apiBaseUrl || ''
  const fallbacks = g.apiFallbackUrls || []
  return [configured]
    .concat(fallbacks)
    .filter(Boolean)
    .map(base => String(base).replace(/\/$/, ''))
    .filter((base, index, all) => all.indexOf(base) === index)
}

function cachedBase() {
  return wx.getStorageSync(STORAGE_KEY) || ''
}

function saveBase(base) {
  _resolved = base
  wx.setStorageSync(STORAGE_KEY, base)
}

// 探测某个 base 是否可达。只要建立 HTTP 连接（任意状态码）都视为网关存在。
// 探测目标是网关自带的 /health（不代理到板卡，秒回），而不是 /api/status：
// /api/status 会经网关代理到板卡，板卡在跑视觉/ASR 时 CPU 忙、响应可能超过
// 超时，导致"明明 EXE 开着却扫不到网关"。用 /health 只要 EXE 在就一定能发现。
function probe(base, timeout = 2000) {
  return new Promise(resolve => {
    wx.request({
      url: base + '/health',
      method: 'GET',
      timeout,
      success: () => resolve(base),
      fail: () => resolve(null)
    })
  })
}

// 依次扫描每个子网，子网内部用并发窗口探测，命中第一个即停。
function discoverGateway() {
  const disc = getGlobal().discovery || {}
  const port = disc.port || 5005
  const subnets = disc.subnets && disc.subnets.length
    ? disc.subnets
    : ['172.20.10', '192.168.5']
  const hostStart = disc.hostStart != null ? disc.hostStart : 2
  const hostEnd = disc.hostEnd != null ? disc.hostEnd : 40
  const CONCURRENT = 8

  const hosts = []
  for (const subnet of subnets) {
    for (let i = hostStart; i <= hostEnd; i++) {
      hosts.push('http://' + subnet + '.' + i + ':' + port)
    }
  }
  if (!hosts.length) return Promise.resolve(null)

  return new Promise(resolve => {
    let index = 0
    let pending = 0
    let done = false

    const finish = value => {
      if (done) return
      done = true
      resolve(value)
    }

    const tick = () => {
      while (pending < CONCURRENT && index < hosts.length && !done) {
        const base = hosts[index++]
        pending += 1
        probe(base).then(found => {
          pending -= 1
          if (found) {
            finish(found)
            return
          }
          tick()
        })
      }
      if (index >= hosts.length && pending === 0 && !done) {
        finish(null)
      }
    }

    tick()
  })
}

// 返回可直接使用的网关 base（配置优先，其次缓存，最后局域网扫描）。
async function resolveBase() {
  if (_resolved) return _resolved

  // 1. 显式配置 + 本机回退：快速试一圈
  for (const base of candidateBases()) {
    if (base && await probe(base)) {
      saveBase(base)
      return base
    }
  }
  // 2. 本地缓存（上次命中的网关）
  const cached = cachedBase()
  if (cached && await probe(cached)) {
    saveBase(cached)
    return cached
  }
  // 3. 局域网自动发现（抄底）
  const found = await discoverGateway()
  if (found) saveBase(found)
  return found
}

function request(path, method = 'GET', data) {
  return new Promise((resolve, reject) => {
    const doRequest = (base, attempt) => {
      wx.request({
        url: base + path,
        method,
        data,
        timeout: 60000,
        header: { 'content-type': 'application/json' },
        success: response => {
          if (response.statusCode >= 200 && response.statusCode < 300) {
            const body = response.data
            if (body && body.error) {
              reject(new Error(body.error + (body.hint ? ': ' + body.hint : '')))
            } else {
              saveBase(base)
              resolve(body)
            }
          } else {
            // 网关通了但返回异常（板卡尚未就绪等），交给上层按状态码提示
            reject(new Error('暖语服务异常 ' + response.statusCode))
          }
        },
        fail: () => {
          if (attempt < 3) {
            // 可能是板卡/EXE 重启导致旧地址失效，重走解析（重新发现）
            _resolved = ''
            setTimeout(() => {
              resolveBase().then(next => {
                if (next) doRequest(next, attempt + 1)
                else reject(new Error('无法连接暖语，请确认手机和电脑在同一热点'))
              })
            }, 600)
          } else {
            reject(new Error('无法连接暖语，请确认手机和电脑在同一热点'))
          }
        }
      })
    }

    resolveBase().then(base => {
      if (!base) {
        reject(new Error('无法连接暖语，请确认手机和电脑在同一热点'))
        return
      }
      doRequest(base, 0)
    }).catch(reject)
  })
}

// 后台预热：App.onLaunch 调用，提前把网关缓存好。
async function prewarm() {
  try {
    await resolveBase()
  } catch (e) {
    // 静默失败，请求时再试
  }
}

// 诊断：返回各探测结果，方便定位"为什么连不上"。
async function diagnose() {
  const report = { configured: [], cached: '', discovery: null, lastError: '' }
  const bases = candidateBases() || []
  for (const base of bases) {
    if (!base) continue
    let found = null
    try { found = await probe(base) } catch (e) {}
    report.configured.push({ base: base, reachable: !!found })
  }
  try { report.cached = cachedBase() || '' } catch (e) {}
  try {
    report.discovery = await discoverGateway()
  } catch (e) {
    report.lastError = String((e && e.message) || e)
  }
  return report
}

module.exports = {
  status: () => request('/api/status'),
  sensors: () => request('/api/sensors'),
  weather: () => request('/api/weather'),
  chat: text => request('/api/chat', 'POST', { text }),
  command: command => request('/api/command', 'POST', { command }),
  mood: mood => request('/api/mood', 'POST', { mood }),
  tts: text => request('/api/tts', 'POST', { text }),
  prewarm,
  resolveBase,
  diagnose
}
