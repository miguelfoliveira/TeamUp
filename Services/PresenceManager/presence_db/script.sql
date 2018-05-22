CREATE TABLE `CHAT_ROOM` (
	`CHAT_ROOM_JID`	VARCHAR(100) NOT NULL PRIMARY KEY
);

CREATE TABLE `CHAT_PRESENCE` (
	`CHAT_ID`	INTEGER NOT NULL PRIMARY KEY AUTO_INCREMENT,
	`CHAT_ROOM_JID`	VARCHAR(100) NOT NULL,
	`USER_JID`	VARCHAR(100) NOT NULL,
	`STATE`	VARCHAR(20) NOT NULL DEFAULT 'offline',
   FOREIGN KEY(`CHAT_ROOM_JID`) REFERENCES `CHAT_ROOM`(`CHAT_ROOM_JID`)
);




