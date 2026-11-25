import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

const DEFAULT_BACKEND_PORT = 12011
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

/**
 * 校验并解析合法端口号（1..65535），非法值返回 null。
 */
function parseValidPort(raw: string | undefined): number | null {
  if (!raw) {
    return null
  }
  const trimmed = raw.trim().replace(/^['"]|['"]$/g, '')
  if (!/^\d+$/.test(trimmed)) {
    return null
  }
  const port = Number(trimmed)
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    return null
  }
  return port
}

/**
 * 从结构化 YAML 配置文件中读取 server.port（忽略注释与 null 空值）。
 */
function readServerPortFromYaml(filePath: string): number | null {
  if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    return null
  }
  const lines = fs.readFileSync(filePath, 'utf-8').split(/\r?\n/)
  let inServerSection = false
  let serverIndent = 0

  for (const line of lines) {
    const stripped = line.replace(/#.*$/, '')
    if (!stripped.trim()) {
      continue
    }
    const indent = stripped.length - stripped.trimStart().length
    const content = stripped.trim()

    if (!inServerSection) {
      if (indent === 0 && /^server\s*:\s*$/.test(content)) {
        inServerSection = true
        serverIndent = indent
      }
      continue
    }

    if (indent <= serverIndent) {
      inServerSection = false
      continue
    }

    const match = content.match(/^port\s*:\s*(.*)$/)
    if (match) {
      const value = match[1].trim()
      if (!value || value === 'null' || value === '~') {
        return null
      }
      return parseValidPort(value)
    }
  }
  return null
}

/**
 * 按与后端一致的优先级（YAML server.port > 环境变量 SERVER_PORT > 默认 12011）解析后端开发端口。
 */
function resolveBackendPort(mode: string): number {
  const env = loadEnv(mode, REPO_ROOT, '')
  const explicitConfigFile = (process.env.CONFIG_FILE || env.CONFIG_FILE || '').trim()
  const candidateFiles = explicitConfigFile
    ? [path.resolve(REPO_ROOT, explicitConfigFile)]
    : [path.join(REPO_ROOT, 'config.yaml'), path.join(REPO_ROOT, 'config.yml')]

  for (const configFile of candidateFiles) {
    const yamlPort = readServerPortFromYaml(configFile)
    if (yamlPort !== null) {
      return yamlPort
    }
    if (fs.existsSync(configFile) && fs.statSync(configFile).isFile()) {
      break
    }
  }

  const envPort = parseValidPort(process.env.SERVER_PORT || env.SERVER_PORT)
  return envPort ?? DEFAULT_BACKEND_PORT
}

export default defineConfig(({ mode }) => {
  const backendPort = resolveBackendPort(mode)
  return {
    plugins: [vue()],
    server: {
      port: 5173,
      proxy: { '/api': { target: `http://localhost:${backendPort}`, changeOrigin: true } },
    },
  }
})
