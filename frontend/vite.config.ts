import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ command }) => ({
  // Relative asset paths in the build, absolute in dev.
  //
  // Vite defaults to base '/', which emits <script src="/assets/index-<hash>.js">
  // — absolute from the DOMAIN root. That only works when the app is served at
  // the root. Hosted in a subdirectory (csaapps.pl/apps/ux-agent_frontend/) the
  // browser asks the domain root for those files, gets 404 on every one, and
  // renders a blank shell: index.html itself returns 200, so it looks like the
  // app is broken rather than misplaced.
  //
  // './' resolves assets relative to index.html, so the same build works at the
  // root, in a subdirectory, or anywhere else the static host puts it. The dev
  // server keeps '/' because the Vite proxy below and HMR are rooted there.
  base: command === 'build' ? './' : '/',
  plugins: [vue()],
  server: {
    proxy: {
      // forward /run to Flask on 8000
      '/run': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        secure: false
      },
      '/progress': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        secure: false
      },
      '/generate-example-persona': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        secure: false
      },
      '/generate-audit-settings': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        secure: false
      },
      // /runs and /runs/<id>[/report.html] — note this also covers the /run prefix,
      // but both target the same backend so the overlap is harmless.
      '/runs': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        secure: false
      },
    }
  }
}))
