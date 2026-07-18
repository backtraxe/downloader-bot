#!/data/data/com.termux/files/usr/bin/bash
# -*- coding: utf-8 -*-
# Termux 首次使用配置脚本
# 用法: bash setup_termux.sh

set -e

# ANSI 颜色
G="\033[32m"
Y="\033[33m"
R="\033[31m"
B="\033[34m"
P="\033[35m"
N="\033[0m"

info()  { echo -e "${G}[INFO]${N} $1"; }
warn()  { echo -e "${Y}[WARN]${N} $1"; }
err()   { echo -e "${R}[ERROR]${N} $1"; }
step()  { echo -e "\n${B}==== $1 ====${N}"; }
ask()   { echo -e "${P}[?]${N} $1 (y/n)"; read -r ans; }

# ─────────────────────────────────────────────
# 第 0 步: 环境检查
# ─────────────────────────────────────────────
step "0/10 环境检查"

if [ ! -d "/data/data/com.termux" ]; then
    err "此脚本仅适用于 Termux 环境"
    exit 1
fi
info "检测到 Termux 环境"

# ─────────────────────────────────────────────
# 第 1 步: 申请存储权限
# ─────────────────────────────────────────────
step "1/10 申请存储权限"

if [ ! -d ~/storage ]; then
    info "申请存储权限（弹窗请在手机上点允许）"
    termux-setup-storage
    sleep 2
    if [ ! -d ~/storage ]; then
        warn "存储权限未授予，下载的文件只能在 Termux 内部访问"
        warn "如需访问手机相册等位置，请重新运行此脚本或手动执行: termux-setup-storage"
    fi
else
    info "存储权限已就绪"
fi

# ─────────────────────────────────────────────
# 第 2 步: 更新 Termux 包
# ─────────────────────────────────────────────
step "2/10 更新 Termux 包"

info "更新包列表和已安装包（首次可能需要几分钟）"
pkg update -y && pkg upgrade -y
info "Termux 包已更新"

# ─────────────────────────────────────────────
# 第 3 步: 安装基础工具
# ─────────────────────────────────────────────
step "3/10 安装基础工具"

info "安装常用工具"
pkg install -y \
    git \
    python \
    python-pip \
    rust \
    clang \
    make \
    pkg-config \
    libffi \
    openssl \
    vim \
    openssh \
    termux-tools \
    tree \
    wget \
    unzip \
    jq \
    htop \
    man \
    coreutils \
    findutils \
    sed \
    gawk \
    tmux \
    fzf \
    ripgrep \
    fd

info "基础工具已安装"

# 升级 pip
info "升级 pip 和打包工具"
pip install --upgrade pip setuptools wheel

# ─────────────────────────────────────────────
# 第 4 步: 配置 pip 镜像源（国内加速）
# ─────────────────────────────────────────────
step "4/10 配置 pip 镜像源"

PIP_CONF=~/.config/pip/pip.conf
mkdir -p ~/.config/pip

if [ -f "$PIP_CONF" ] && grep -q "aliyun" "$PIP_CONF" 2>/dev/null; then
    info "pip 镜像源已配置，跳过"
else
    cat > "$PIP_CONF" << 'EOF'
[global]
index-url = https://mirrors.aliyun.com/pypi/simple/
trusted-host = mirrors.aliyun.com
EOF
    info "已配置阿里云 pip 镜像源"
fi

# ─────────────────────────────────────────────
# 第 5 步: 配置 Git
# ─────────────────────────────────────────────
step "5/10 配置 Git"

GIT_NAME=$(git config --global user.name 2>/dev/null || true)
GIT_EMAIL=$(git config --global user.email 2>/dev/null || true)

if [ -z "$GIT_NAME" ]; then
    echo -e "${P}[?]${N} 请输入 Git 用户名（回车跳过）:"
    read -r input_name
    [ -n "$input_name" ] && git config --global user.name "$input_name"
else
    info "Git 用户名已设置: $GIT_NAME"
fi

if [ -z "$GIT_EMAIL" ]; then
    echo -e "${P}[?]${N} 请输入 Git 邮箱（回车跳过）:"
    read -r input_email
    [ -n "$input_email" ] && git config --global user.email "$input_email"
else
    info "Git 邮箱已设置: $GIT_EMAIL"
fi

# 通用配置
git config --global init.defaultBranch main
git config --global pull.rebase false
git config --global core.editor vim
git config --global alias.co checkout
git config --global alias.br branch
git config --global alias.ci commit
git config --global alias.st status
git config --global alias.lg "log --oneline --graph --decorate --all"
info "Git 通用配置已写入"

# ─────────────────────────────────────────────
# 第 6 步: SSH 密钥
# ─────────────────────────────────────────────
step "6/10 SSH 密钥"

if [ -f ~/.ssh/id_ed25519 ]; then
    info "SSH 密钥已存在，跳过生成"
else
    echo -e "${P}[?]${N} 是否生成 SSH 密钥（用于 GitHub/GitLab 等）? (y/n)"
    read -r gen_ssh
    if [ "$gen_ssh" = "y" ] || [ "$gen_ssh" = "Y" ]; then
        echo -e "${P}[?]${N} 请输入邮箱（作为 SSH key 注释，回车用 Git 邮箱）:"
        read -r ssh_email
        ssh_email=${ssh_email:-$input_email}
        ssh-keygen -t ed25519 -C "${ssh_email}" -f ~/.ssh/id_ed25519 -N ""
        info "SSH 密钥已生成: ~/.ssh/id_ed25519"
        echo ""
        echo -e "${B}公钥内容（复制到 GitHub → Settings → SSH Keys）:${N}"
        echo "---"
        cat ~/.ssh/id_ed25519.pub
        echo "---"
        echo ""
        warn "请将上面的公钥添加到 GitHub/GitLab"
    else
        info "跳过 SSH 密钥生成，之后可手动: ssh-keygen -t ed25519 -C \"邮箱\""
    fi
fi

# 启动 ssh-agent 并添加密钥
if [ -f ~/.ssh/id_ed25519 ]; then
    eval "$(ssh-agent -s)" 2>/dev/null
    ssh-add ~/.ssh/id_ed25519 2>/dev/null || true
    info "ssh-agent 已启动并加载密钥"
fi

# ─────────────────────────────────────────────
# 第 7 步: Zsh + Oh My Zsh
# ─────────────────────────────────────────────
step "7/10 Zsh + Oh My Zsh"

if [ -f ~/.zshrc ] && grep -q "oh-my-zsh" ~/.zshrc 2>/dev/null; then
    info "Oh My Zsh 已安装，跳过"
else
    echo -e "${P}[?]${N} 是否安装 Zsh + Oh My Zsh? (y/n)"
    read -r install_zsh
    if [ "$install_zsh" = "y" ] || [ "$install_zsh" = "Y" ]; then
        info "安装 Zsh"
        pkg install -y zsh

        info "安装 Oh My Zsh"
        # 手动克隆，sh 安装脚本在 Termux 上兼容性问题较多
        ZSH_CUSTOM=${ZSH_CUSTOM:-~/.oh-my-zsh}
        if [ ! -d "$ZSH_CUSTOM" ]; then
            git clone --depth=1 https://github.com/ohmyzsh/ohmyzsh.git "$ZSH_CUSTOM"
        fi

        # 安装常用插件
        info "安装插件: zsh-autosuggestions, zsh-syntax-highlighting"
        ZSH_CUSTOM_DIR="$ZSH_CUSTOM/custom"
        mkdir -p "$ZSH_CUSTOM_DIR/plugins"

        if [ ! -d "$ZSH_CUSTOM_DIR/plugins/zsh-autosuggestions" ]; then
            git clone --depth=1 https://github.com/zsh-users/zsh-autosuggestions \
                "$ZSH_CUSTOM_DIR/plugins/zsh-autosuggestions"
        fi
        if [ ! -d "$ZSH_CUSTOM_DIR/plugins/zsh-syntax-highlighting" ]; then
            git clone --depth=1 https://github.com/zsh-users/zsh-syntax-highlighting \
                "$ZSH_CUSTOM_DIR/plugins/zsh-syntax-highlighting"
        fi

        # 生成 .zshrc
        cat > ~/.zshrc << 'EOF'
# Oh My Zsh 配置
export ZSH="$HOME/.oh-my-zsh"

# 主题
ZSH_THEME="agnoster"

# 插件
plugins=(
    git
    z
    zsh-autosuggestions
    zsh-syntax-highlighting
)

source $ZSH/oh-my-zsh.sh

# 别名
alias ll="ls -lh"
alias la="ls -ah"
alias cls="clear"
alias py="python"
alias pipi="pip install"
alias g="git"
alias gs="git status"
alias gl="git log --oneline -10"

# 历史记录
HISTSIZE=10000
SAVEHIST=10000
setopt HIST_IGNORE_DUPS
setopt SHARE_HISTORY

# 键绑定
bindkey '^A' beginning-of-line
bindkey '^E' end-of-line
bindkey '^R' history-incremental-search-backward
EOF

        info "Zsh + Oh My Zsh 安装完成"
        warn "切换默认 Shell: chsh -s zsh（输入后重启 Termux 生效）"
    else
        info "跳过 Zsh 安装，之后可手动执行此脚本"
    fi
fi

# ─────────────────────────────────────────────
# 第 8 步: Vim 配置
# ─────────────────────────────────────────────
step "8/10 Vim 配置"

if [ -f ~/.vimrc ] && grep -q "downloader-bot" ~/.vimrc 2>/dev/null; then
    info "Vim 配置已存在，跳过"
else
    cat > ~/.vimrc << 'EOF'
" downloader-bot Termux Vim 配置
set nocompatible
syntax on
filetype plugin indent on

" 编码
set encoding=utf-8
set fileencoding=utf-8

" 缩进
set tabstop=4
set shiftwidth=4
set expandtab
set autoindent
set smartindent

" 显示
set number
set relativenumber
set cursorline
set showmatch
set showcmd

" 搜索
set hlsearch
set incsearch
set ignorecase
set smartcase

" 行为
set backspace=indent,eol,start
set autoread
set hidden
set wildmenu
set laststatus=2

" 状态栏
set statusline=%F%m%r%h%w\ [FORMAT=%{&ff}]\ [TYPE=%Y]\ [POS=%l,%v][%p%%]

" 不创建交换文件和备份
set noswapfile
set nobackup
set nowritebackup

" 括号匹配高亮
let g:loaded_matchparen=1

" Python 专用
autocmd FileType python setlocal tabstop=4 shiftwidth=4 expandtab
autocmd FileType python setlocal colorcolumn=80

" 快捷键
nnoremap <C-s> :w<CR>
inoremap <C-s> <Esc>:w<CR>
nnoremap <C-q> :q<CR>
EOF
    info "Vim 配置已写入 ~/.vimrc"
fi

# ─────────────────────────────────────────────
# 第 9 步: Tmux 配置
# ─────────────────────────────────────────────
step "9/10 Tmux 配置"

if [ -f ~/.tmux.conf ] && grep -q "downloader-bot" ~/.tmux.conf 2>/dev/null; then
    info "Tmux 配置已存在，跳过"
else
    cat > ~/.tmux.conf << 'EOF'
# downloader-bot Termux Tmux 配置
# 前缀键改为 Ctrl+a（手机上更容易按）
set -g prefix C-a
unbind C-b
bind C-a send-prefix

# 基本设置
set -g default-terminal "screen-256color"
set -g history-limit 10000
set -g mouse on
setw -g mode-keys vi

# 窗口编号从 1 开始
set -g base-index 1
setw -g pane-base-index 1
set -g renumber-windows on

# 分屏快捷键（更直觉）
bind | split-window -h -c "#{pane_current_path}"
bind - split-window -v -c "#{pane_current_path}"
unbind '"'
unbind %

# 切换窗格
bind h select-pane -L
bind j select-pane -D
bind k select-pane -U
bind l select-pane -R

# 重新加载配置
bind r source-file ~/.tmux.conf \; display "Tmux 配置已重新加载"

# 状态栏
set -g status-style bg=default,fg=white
set -g status-left "[#S] "
set -g status-right "%H | %Y-%m-%d %H:%M "
setw -g window-status-current-style fg=black,bg=green
EOF
    info "Tmux 配置已写入 ~/.tmux.conf"
fi

# ─────────────────────────────────────────────
# 第 10 步: 完成提示
# ─────────────────────────────────────────────
step "10/10 完成"

echo -e "${G}Termux 初始化完成！${N}"
echo ""
echo "已安装:"
echo -e "  ${B}git${N}          — 版本控制"
echo -e "  ${B}python${N}       — Python $(python --version 2>&1 | awk '{print $2}')"
echo -e "  ${B}pip${N}          — Python 包管理（镜像源: 阿里云）"
echo -e "  ${B}vim${N}          — 文本编辑器（已配置 ~/.vimrc）"
echo -e "  ${B}openssh${N}      — SSH 远程连接"
echo -e "  ${B}rust/clang${N}   — 编译工具链"
echo -e "  ${B}tmux${N}         — 终端复用器（已配置 ~/.tmux.conf）"
echo -e "  ${B}zsh/oh-my-zsh${N}— Zsh + Oh My Zsh（需 chsh -s zsh 重启生效）"
echo -e "  ${B}fzf/ripgrep/fd${N}— 模糊搜索 / 快速查找"
echo ""
echo "已生成配置文件:"
echo -e "  ${B}~/.gitconfig${N}     — Git 配置"
echo -e "  ${B}~/.ssh/id_ed25519${N} — SSH 密钥"
echo -e "  ${B}~/.zshrc${N}          — Zsh 配置"
echo -e "  ${B}~/.vimrc${N}          — Vim 配置"
echo -e "  ${B}~/.tmux.conf${N}      — Tmux 配置"
echo -e "  ${B}~/.config/pip/pip.conf${N} — pip 镜像源"
echo ""
echo "后续步骤:"
echo -e "  1. ${B}chsh -s zsh${N}              切换默认 Shell（需重启 Termux）"
echo -e "  2. ${B}cat ~/.ssh/id_ed25519.pub${N} 查看 SSH 公钥，添加到 GitHub"
echo -e "  3. ${B}ssh -T git@github.com${N}     验证 GitHub SSH 连接"
echo -e "  4. ${B}git clone <仓库>${N}         克隆项目"
echo ""
echo "常用命令:"
echo -e "  ${B}python --version${N}       查看版本"
echo -e "  ${B}pip install <包名>${N}     安装 Python 包"
echo -e "  ${B}ssh -T git@github.com${N}  测试 GitHub 连接"
echo -e "  ${B}pkg install <包名>${N}    安装 Termux 包"
echo -e "  ${B}pkg search <关键词>${N}   搜索可用包"
echo -e "  ${B}tmux${N}                  启动 tmux 会话"
echo -e "  ${B}tmux a${N}                重新接入会话"
