/**
 * 화면에 나가는 한국어 문자열을 전부 여기 모은다.
 *
 * MVP 는 한국어 전용이지만, 문자열이 컴포넌트에 흩어져 있으면 나중에 영어를
 * 붙일 때 전수 조사를 해야 한다. 지금 한 곳에 모아두는 비용이 훨씬 싸다.
 * 영어를 추가할 때는 이 객체와 같은 모양의 en 객체를 만들고 훅으로 고르면 된다.
 */
export const ko = {
  brand: "Dropship",
  tagline: "링크 하나로 보내고, 정해진 시간이 지나면 사라집니다",

  upload: {
    dropHere: "여기에 파일을 끌어다 놓으세요",
    orBrowse: "또는 클릭해서 선택",
    pasteHint: "붙여넣기(Ctrl+V)로도 추가할 수 있어요",
    addMore: "파일 추가",
    clear: "전부 지우기",
    send: "보내기",
    sending: "보내는 중…",
    preparing: "준비 중…",
    finishing: "마무리 중…",
    cancel: "취소",
    expiryLabel: "보관 기간",
    fileCount: (n: number) => `파일 ${n}개`,
    remove: "제거",
  },

  result: {
    title: "링크가 만들어졌습니다",
    copyLink: "링크 복사",
    copied: "복사했습니다",
    qrHint: "다른 기기에서 QR을 스캔하면 바로 받을 수 있어요",
    expiresAt: (text: string) => `${text} 후 자동으로 삭제됩니다`,
    sendAnother: "다른 파일 보내기",
    deleteNow: "지금 삭제",
    deleting: "삭제 중…",
    deleted: "삭제했습니다",
    deleteConfirm: "이 링크를 지금 삭제할까요? 되돌릴 수 없습니다.",
    ownerNotice:
      "이 화면을 닫으면 삭제 권한이 사라집니다. 링크는 기간이 지나면 알아서 삭제돼요.",
  },

  receive: {
    title: "받은 파일",
    download: "받기",
    downloadAll: "전체 받기",
    preparing: "준비 중…",
    totalSize: (text: string) => `전체 ${text}`,
    riskyWarning: "실행 파일이 포함돼 있습니다. 보낸 사람을 확인한 뒤 여세요.",
    expiresIn: (text: string) => `${text} 후 만료`,
    expired: "만료됨",
  },

  gone: {
    title: "링크를 찾을 수 없습니다",
    body: "주소가 잘못되었거나, 보관 기간이 지나 파일이 삭제되었습니다.",
    home: "파일 보내기",
  },

  notFound: {
    title: "페이지를 찾을 수 없습니다",
    home: "처음으로",
  },

  errors: {
    tooLarge: "전송 합계가 1GB를 넘습니다. 파일을 줄여 주세요.",
    tooMany: (max: number) => `한 번에 보낼 수 있는 파일은 ${max}개까지입니다.`,
    empty: "빈 파일은 보낼 수 없습니다.",
    rateLimited: "요청이 너무 잦습니다. 잠시 후 다시 시도해 주세요.",
    quota: "오늘 업로드 한도를 모두 사용했습니다.",
    network: "네트워크 오류가 발생했습니다. 다시 시도해 주세요.",
    uploadFailed: "업로드에 실패했습니다. 다시 시도해 주세요.",
    canceled: "업로드를 취소했습니다.",
    generic: "문제가 발생했습니다. 다시 시도해 주세요.",
  },

  safety: {
    notice:
      "링크를 아는 사람은 누구나 받을 수 있습니다. 민감한 파일은 보내지 마세요.",
  },

  admin: {
    title: "서버 상태",
    updated: (text: string) => `${text} 갱신`,
    signIn: "관리자 토큰을 입력하세요",
    signInHint: "서버의 /etc/dropship/dropship.env 에 있는 ADMIN_TOKEN 값입니다.",
    tokenPlaceholder: "ADMIN_TOKEN",
    enter: "확인",
    signOut: "토큰 지우기",
    wrongToken: "토큰이 올바르지 않습니다.",
    disabled: "이 서버는 관리자 기능이 꺼져 있습니다.",
    disabledHint: "ADMIN_TOKEN 을 설정하고 서비스를 재시작하면 켜집니다.",
    loading: "불러오는 중…",

    system: "시스템",
    memory: "메모리",
    cpu: "CPU",
    disk: "디스크",
    swap: "스왑",
    load: "부하",
    uptime: "가동 시간",

    services: "서비스",
    serviceActive: "실행 중",
    serviceInactive: "중지됨",
    serviceFailed: "실패",
    serviceUnknown: "알 수 없음",

    transfers: "전송",
    live: "유효한 링크",
    pending: "업로드 중",
    expiring: "1시간 내 만료",
    overdue: "만료 후 미삭제",
    overdueWarn: "Sweeper 가 멈췄을 수 있습니다",
    storedFiles: "보관 파일",

    activity: "오늘",
    uploadsToday: "업로드",
    bytesToday: "업로드 용량",
    ipsToday: "고유 IP",
    downloadsTotal: "누적 다운로드",

    sweeper: "마지막 정리",
    sweeperNever: "기록 없음",
    sweeperResult: (t: number, o: number) => `전송 ${t}건 · 객체 ${o}개 삭제`,

    list: "업로드 목록",
    listEmpty: "업로드된 전송이 없습니다.",
    colCode: "코드",
    colFiles: "파일",
    colSize: "크기",
    colExpires: "만료",
    colDownloads: "받음",
    colUploader: "업로더",
    colAction: "",
    statusReady: "유효",
    statusPending: "업로드 중",
    expired: "만료됨",
    filterAll: "전체",
    more: "더 보기",
    showing: (shown: number, total: number) => `${total}건 중 ${shown}건`,
    deleteOne: "삭제",
    deleting: "삭제 중…",
    deleteConfirm: (code: string) =>
      `${code} 를 삭제할까요?
파일이 즉시 지워지고 링크가 죽습니다. 되돌릴 수 없습니다.`,
    deleteFailed: "삭제하지 못했습니다.",
    moreFiles: (n: number) => `외 ${n}개`,

    // 미터 상태. 색만으로 읽히지 않도록 항상 이 글자를 함께 보여준다.
    stateOk: "여유",
    stateWarn: "주의",
    stateHigh: "높음",
    stateCritical: "위험",
    unavailable: "측정 불가",
  },

  expiryOptions: {
    3600: "1시간",
    21600: "6시간",
    86400: "24시간",
    259200: "3일",
    604800: "7일",
  } as Record<number, string>,
} as const;

export type Messages = typeof ko;
