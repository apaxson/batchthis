import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: path.resolve(__dirname, '../apps/batchthis/static/batchthis/dist'),
    emptyOutDir: true,
    rollupOptions: {
      input: {
        'model-select': path.resolve(__dirname, 'src/entries/model-select.jsx'),
      },
      output: {
        entryFileNames: '[name].js',
        assetFileNames: '[name][extname]',
      },
    },
  },
})
