<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { Currency, WatchlistItem } from '../api/types'
import { useWatchlistStore } from '../stores/watchlist'
import { formatMoney, priceDigits } from '../utils/format'
import { useViewport } from '../utils/viewport'

const props = defineProps<{
  securityId: string
  accountId?: string
  symbolLabel: string
  securityName?: string | null
  currency?: Currency | null
  isFund?: boolean
  existing?: WatchlistItem | null
  lastSellPrice?: string | null
  currentPrice?: string | null
}>()

const emit = defineEmits<{
  saved: [item: WatchlistItem]
  removed: [securityId: string]
}>()

const visible = defineModel<boolean>({ default: false })
const watchlistStore = useWatchlistStore()
const { isMobile } = useViewport()
const saving = ref(false)
const removing = ref(false)

const form = reactive({
  targetBuyPrice: '',
  alertEnabled: false,
  note: '',
})

const isWatched = computed(() => Boolean(props.existing))
const hasValidPrice = computed(() => {
  const raw = form.targetBuyPrice.trim()
  if (!raw) return false
  const parsed = Number(raw)
  return Number.isFinite(parsed) && parsed > 0
})

/** 将小数字符串末尾多余零去掉，保留人类可读格式。 */
function trimPriceZeros(raw?: string | null): string {
  const text = (raw ?? '').trim()
  if (!text) return ''
  if (!text.includes('.')) return text
  return text.replace(/(\.\d*?[1-9])0+$/, '$1').replace(/\.0+$/, '')
}

watch(
  visible,
  (open) => {
    if (!open) return
    form.targetBuyPrice = trimPriceZeros(props.existing?.targetBuyPrice)
    form.alertEnabled = Boolean(props.existing?.alertEnabled)
    form.note = props.existing?.note ?? ''
  },
  { immediate: true },
)

watch(
  () => form.targetBuyPrice,
  (next) => {
    if (!next.trim() && form.alertEnabled) {
      form.alertEnabled = false
    }
  },
)

/** 保存关注状态与目标买回价/提醒配置。 */
async function submit(): Promise<void> {
  if (!props.securityId || saving.value) return
  const rawPrice = form.targetBuyPrice.trim()
  if (rawPrice && !hasValidPrice.value) {
    ElMessage.warning('目标买回价必须为大于 0 的有效数字')
    return
  }
  if (form.alertEnabled && !hasValidPrice.value) {
    ElMessage.warning('开启到价提醒前请先填写有效的目标买回价')
    return
  }
  saving.value = true
  try {
    const noteText = form.note.trim() || null
    const saved = props.existing
      ? await watchlistStore.update(
          props.securityId,
          {
            targetBuyPrice: rawPrice || null,
            alertEnabled: rawPrice ? form.alertEnabled : false,
            note: noteText,
          },
          props.accountId,
        )
      : await watchlistStore.add(
          {
            securityId: props.securityId,
            accountId: props.accountId,
            targetBuyPrice: rawPrice || null,
            alertEnabled: rawPrice ? form.alertEnabled : false,
            note: noteText,
          },
          props.accountId,
        )
    ElMessage.success(props.existing ? '关注与买回价设置已更新' : '已加入关注列表')
    visible.value = false
    emit('saved', saved)
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : '保存关注设置失败')
  } finally {
    saving.value = false
  }
}

/** 取消关注当前证券。 */
async function handleRemove(): Promise<void> {
  if (!props.securityId || removing.value) return
  removing.value = true
  try {
    await watchlistStore.remove(props.securityId, props.accountId)
    ElMessage.success('已取消关注')
    visible.value = false
    emit('removed', props.securityId)
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : '取消关注失败')
  } finally {
    removing.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="isWatched ? '关注与目标买回价设置' : '加入关注并设置买回价'"
    :width="isMobile ? '94vw' : '460px'"
  >
    <div class="wl-dialog-head">
      <div class="wl-sec-title">
        <strong>{{ symbolLabel }}</strong>
        <span v-if="securityName" class="wl-sec-name">{{ securityName }}</span>
      </div>
      <div class="wl-ref-row muted">
        <span v-if="currentPrice">现价 {{ formatMoney(currentPrice, currency, priceDigits(isFund, 3)) }}</span>
        <span v-if="lastSellPrice">上次卖出 {{ formatMoney(lastSellPrice, currency, priceDigits(isFund, 3)) }}</span>
      </div>
    </div>

    <el-form :label-position="isMobile ? 'top' : 'right'" :label-width="isMobile ? undefined : '104px'">
      <el-form-item :label="isFund ? '目标买回净值' : '目标买回价'">
        <el-input
          v-model="form.targetBuyPrice"
          inputmode="decimal"
          clearable
          :placeholder="isFund ? '留空表示仅关注，如 1.1500' : '留空表示仅关注，如 1380.00'"
        />
      </el-form-item>
      <el-form-item label="企微到价提醒">
        <div class="wl-alert-row">
          <el-switch v-model="form.alertEnabled" :disabled="!hasValidPrice" />
          <span class="muted wl-alert-hint">
            {{ hasValidPrice ? '盘中现价 ≤ 目标买回价时推送企微（空仓与持仓均触发，同日同价去重）' : '填写目标买回价后可开启提醒' }}
          </span>
        </div>
      </el-form-item>
      <el-form-item label="关注备注">
        <el-input
          v-model="form.note"
          type="textarea"
          :rows="2"
          maxlength="256"
          show-word-limit
          placeholder="可选：记录回接逻辑或分批加仓计划"
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <div class="wl-dialog-footer">
        <el-button v-if="isWatched" type="danger" plain :loading="removing" @click="handleRemove">
          取消关注
        </el-button>
        <div class="wl-footer-right">
          <el-button @click="visible = false">关闭</el-button>
          <el-button type="primary" :loading="saving" @click="submit">保存</el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.wl-dialog-head {
  margin-bottom: 14px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.wl-sec-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 14px;
}
.wl-sec-name {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.wl-ref-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 4px;
  font-size: 12px;
}
.wl-alert-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.wl-alert-hint {
  font-size: 12px;
  line-height: 1.4;
}
.wl-dialog-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.wl-footer-right {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
</style>
