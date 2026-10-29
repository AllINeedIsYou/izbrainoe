#!/bin/zsh
cd "${0:A:h}"
[[ -f 'Настройки.env' ]] || cp .env.example 'Настройки.env'
chmod 600 'Настройки.env'
open -a TextEdit 'Настройки.env'
