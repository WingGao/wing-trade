<template>
  <el-container class="shell">
    <el-aside width="220px" class="sidebar">
      <div class="brand"><span class="brand-mark">翼</span><div><strong>A 股交易账本</strong><small>Portfolio Journal</small></div></div>
      <el-menu :default-active="$route.path" router class="nav" background-color="transparent" text-color="#a7b1c2" active-text-color="#fff">
        <el-menu-item index="/dashboard">总览 Dashboard</el-menu-item>
        <el-menu-item index="/trades">交易流水 Trades</el-menu-item>
        <el-menu-item index="/analytics">复盘分析 Analytics</el-menu-item>
      </el-menu>
      <div class="sidebar-foot">账本优先 · 行情仅作参考</div>
    </el-aside>
    <el-container>
      <el-header class="topbar"><div><span class="eyebrow">自托管投资工作台</span><h1>{{ $route.path === '/dashboard' ? '总览' : ($route.meta.title ?? '投资账本') }}</h1></div><el-tag type="success" effect="dark">本地账本</el-tag></el-header>
      <el-main class="main"><router-view /></el-main>
    </el-container>
  </el-container>
</template>
