<template>
  <div>
    <div class="page-head">
      <div>
        <h2>用户与邀请管理</h2>
        <div class="muted">仅管理员可邀请新用户；各用户的交易账本与止盈配置完全隔离。</div>
      </div>
      <el-button :loading="loading" @click="loadUsers">刷新列表</el-button>
    </div>

    <el-card class="panel">
      <template #header><span class="panel-title">邀请新用户</span></template>
      <el-form inline @submit.prevent="submitInvite" class="invite-form">
        <el-form-item label="受邀邮箱">
          <el-input
            v-model="inviteEmail"
            type="email"
            placeholder="例如 member@example.com"
            style="width: 280px"
          />
        </el-form-item>
        <el-form-item label="显示名称（可选）">
          <el-input
            v-model="inviteDisplayName"
            placeholder="例如 张三"
            style="width: 180px"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" native-type="submit" :loading="inviting">
            发送邀请
          </el-button>
        </el-form-item>
      </el-form>
      <div class="muted">
        受邀人直接前往登录页输入该邮箱获取验证码，首次验证通过后即自动激活并创建独立默认账户。
      </div>
    </el-card>

    <el-card class="panel">
      <template #header><span class="panel-title">用户与邀请列表（{{ users.length }}）</span></template>
      <el-table :data="users" v-loading="loading" stripe>
        <el-table-column prop="email" label="邮箱" min-width="220" />
        <el-table-column label="显示名称" min-width="130">
          <template #default="{ row }">{{ row.displayName || '—' }}</template>
        </el-table-column>
        <el-table-column label="角色" width="120">
          <template #default="{ row }">
            <el-tag :type="row.role === 'ADMIN' ? 'danger' : 'info'" size="small">
              {{ row.role === 'ADMIN' ? '管理员' : '普通用户' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近登录" min-width="170">
          <template #default="{ row }">{{ formatDateTime(row.lastLoginAt) }}</template>
        </el-table-column>
        <el-table-column label="创建时间" min-width="170">
          <template #default="{ row }">{{ formatDateTime(row.createdAt) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <span v-if="row.isDefaultAdmin || row.id === auth.user?.id" class="muted">
              当前/默认管理员
            </span>
            <el-button
              v-else-if="row.status === 'INVITED'"
              type="danger"
              link
              :loading="busyUserId === row.id"
              @click="handleRevokeInvite(row)"
            >
              撤销邀请
            </el-button>
            <el-button
              v-else-if="row.status === 'ACTIVE'"
              type="warning"
              link
              :loading="busyUserId === row.id"
              @click="handleToggleStatus(row, 'DISABLED')"
            >
              禁用账号
            </el-button>
            <el-button
              v-else
              type="success"
              link
              :loading="busyUserId === row.id"
              @click="handleToggleStatus(row, 'ACTIVE')"
            >
              重新启用
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import type { AuthUser, UserStatus } from '../api/types'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const users = ref<AuthUser[]>([])
const loading = ref(false)
const inviting = ref(false)
const busyUserId = ref<string | null>(null)
const inviteEmail = ref('')
const inviteDisplayName = ref('')

/** 将用户状态码转换为中文标签文本。 */
function statusLabel(status: UserStatus): string {
  if (status === 'ACTIVE') return '正常'
  if (status === 'INVITED') return '待激活'
  return '已禁用'
}

/** 根据用户状态返回 Element Plus 标签色。 */
function statusTagType(status: UserStatus): 'success' | 'warning' | 'danger' {
  if (status === 'ACTIVE') return 'success'
  if (status === 'INVITED') return 'warning'
  return 'danger'
}

/** 格式化 ISO 时间为本地可读时间。 */
function formatDateTime(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

/** 拉取管理员可见的全部用户与邀请记录。 */
async function loadUsers(): Promise<void> {
  loading.value = true
  try {
    users.value = await api.adminUsers()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加载用户列表失败')
  } finally {
    loading.value = false
  }
}

/** 提交管理员定向邀请请求并刷新用户表格。 */
async function submitInvite(): Promise<void> {
  const email = inviteEmail.value.trim()
  if (!email) {
    ElMessage.warning('请输入受邀邮箱')
    return
  }
  inviting.value = true
  try {
    const result = await api.adminInviteUser({
      email,
      displayName: inviteDisplayName.value.trim() || null,
    })
    inviteEmail.value = ''
    inviteDisplayName.value = ''
    if (result.inviteEmailSent) {
      ElMessage.success(`已邀请 ${result.user.email}，并发送邀请提示邮件`)
    } else {
      ElMessage.warning(`已创建邀请记录（${result.user.email}），但提示邮件发送失败，请线下通知对方登录`)
    }
    await loadUsers()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '邀请失败')
  } finally {
    inviting.value = false
  }
}

/** 撤销尚未首登激活的邀请记录。 */
async function handleRevokeInvite(target: AuthUser): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认撤销对 ${target.email} 的邀请吗？`, '撤销邀请', {
      type: 'warning',
      confirmButtonText: '确认撤销',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  busyUserId.value = target.id
  try {
    await api.adminRevokeInvite(target.id)
    ElMessage.success('邀请已撤销')
    await loadUsers()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '撤销邀请失败')
  } finally {
    busyUserId.value = null
  }
}

/** 禁用或重新启用普通用户账号。 */
async function handleToggleStatus(target: AuthUser, nextStatus: 'ACTIVE' | 'DISABLED'): Promise<void> {
  const actionText = nextStatus === 'DISABLED' ? '禁用' : '启用'
  try {
    await ElMessageBox.confirm(
      nextStatus === 'DISABLED'
        ? `禁用 ${target.email} 后，其当前登录会话将立即失效，确认继续？`
        : `确认重新启用 ${target.email} 吗？`,
      `${actionText}用户`,
      {
        type: 'warning',
        confirmButtonText: `确认${actionText}`,
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }
  busyUserId.value = target.id
  try {
    await api.adminUpdateUserStatus(target.id, nextStatus)
    ElMessage.success(`已${actionText} ${target.email}`)
    await loadUsers()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : `${actionText}失败`)
  } finally {
    busyUserId.value = null
  }
}

onMounted(loadUsers)
</script>
